from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

import database
from database import begin_sql_debug_capture, end_sql_debug_capture
from ai_llm import (
    DEEPSEEK_MODEL,
    call_deepseek_chat,
    get_deepseek_client,
    get_deepseek_llm,
    is_ai_available,
    sanitize_error_message,
)
from ai_permissions import (
    build_user_context,
    check_action_permission,
    check_permission,
    get_allowed_dashboard_departments,
)
from ai_prompts import (
    ROLE_EXAMPLE_ACTIONS,
    WRITE_OPERATION_MESSAGE,
    get_action_system_prompt,
    get_analysis_intent_system_prompt,
    get_intent_system_prompt,
    get_operation_classifier_prompt,
    get_plan_system_prompt,
    get_summary_system_prompt,
)
from ai_schemas import ActionIntentResult, OperationTypeResult, QueryFilters, IntentResult, PlanStep, QueryPlanResult
from ai_tools import (
    add_action_log_action,
    assign_ticket_action,
    canonicalize_action_payload,
    canonicalize_query_filters,
    close_ticket_action,
    create_complaint_action,
    create_ticket_for_complaint_action,
    delete_action_log_action,
    delete_complaint_action,
    delete_order_action,
    delete_ticket_action,
    escalate_ticket_action,
    get_ticket_detail,
    query_action_logs,
    query_assignments,
    query_complaints,
    query_complaint_type_rules,
    query_dashboard_summary,
    query_driver_order_stats,
    query_escalations,
    query_feedback,
    query_feedback_stats,
    query_orders,
    query_risk_tickets,
    query_ticket_lifecycle,
    query_tickets,
    reopen_ticket_action,
    revoke_assignment_action,
    set_pending_feedback_action,
    suggest_ticket_action,
    submit_feedback_action,
    update_complaint_urgency_action,
    update_order_status_action,
    update_ticket_priority_action,
)

try:
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:  # pragma: no cover
    HumanMessage = None
    SystemMessage = None

WRITE_VERBS = (
    "关闭",
    "分派",
    "删除",
    "修改",
    "更新",
    "升级",
    "重开",
    "新增",
    "创建",
    "提交反馈",
    "提交",
    "派给",
    "撤销",
)
QUERY_HINTS = ("查询", "列出", "哪些", "多少", "统计", "有没有", "查看", "帮我查", "给我看")
PERMISSION_SENSITIVE_PATTERNS = (
    "所有员工",
    "全部员工",
    "全量工单",
    "所有工单",
    "全部工单",
    "各部门",
    "部门统计",
    "所有部门",
    "全部手机号",
    "所有乘客手机号",
)
DETAIL_HINTS = ("详情", "详细", "完整", "经过", "进展", "处理情况", "闭环", "信息")
DASHBOARD_HINTS = ("统计", "汇总", "概览", "排名", "排行", "趋势", "看板", "数量", "多少", "最多", "最少")
RISK_HINTS = ("超时", "风险", "升级", "待反馈", "P1", "高优先级", "安全事件", "低满意度")
NESTED_CONNECTORS = ("先", "再", "然后", "并列出", "并查看", "并找出", "随后", "相关工单", "该部门", "该投诉类型")

ANALYSIS_QUERY_KINDS = {
    "sla_risk_scan",
    "complaint_type_quality",
    "driver_service_risk",
    "employee_efficiency_anomaly",
    "conversion_consistency_audit",
    "escalation_effectiveness",
    "customer_service_balance",
    "high_value_order_risk",
    "ticket_risk_scoring",
    "system_health_report",
}


DEPARTMENT_ALIAS_MAP = {
    "客服部": "客服部",
    "客服": "客服部",
    "财务售后部": "财务售后部",
    "财务部": "财务售后部",
    "财务": "财务售后部",
    "安全部": "安全部",
    "安全": "安全部",
    "运营部": "运营部",
    "运营": "运营部",
    "技术支持部": "技术支持部",
    "技术部": "技术支持部",
    "技术": "技术支持部",
    "系统管理部": "系统管理部",
    "系统管理": "系统管理部",
}


def get_ai_runtime_status() -> dict:
    if not is_ai_available():
        return {
            "available": False,
            "model_name": DEEPSEEK_MODEL,
            "message": "DeepSeek API Key 仍为占位符，智能助手暂不可用。",
        }

    if get_deepseek_client() is None or get_deepseek_llm() is None:
        return {
            "available": False,
            "model_name": DEEPSEEK_MODEL,
            "message": "DeepSeek 依赖未正确安装，智能助手暂不可用。",
        }

    return {
        "available": True,
        "model_name": DEEPSEEK_MODEL,
        "message": f"当前使用模型：{DEEPSEEK_MODEL}",
    }


def detect_write_operation_request(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    analysis_hints = (
        "分析",
        "统计",
        "汇总",
        "健康状况",
        "风险评级",
        "综合绩效",
        "排名",
        "排行",
        "比例",
        "分布",
        "平均",
        "是否发生过",
        "是否存在",
        "输出",
        "给出",
    )
    if any(hint in text for hint in QUERY_HINTS) and not any(text.startswith(verb) for verb in WRITE_VERBS):
        return False
    if any(hint in text for hint in analysis_hints) and not any(text.startswith(verb) for verb in WRITE_VERBS):
        return False
    if any(verb in text for verb in WRITE_VERBS) and any(token in text for token in ("工单", "投诉", "反馈", "记录")):
        return True
    if re.search(r"(帮我|请).*(关闭|分派|删除|修改|升级|重开)", text):
        return True
    return False


def build_read_only_response() -> dict:
    return {
        "ok": True,
        "answer": WRITE_OPERATION_MESSAGE,
        "intent": "action_suggestion",
        "data_count": 0,
        "risk_flags": [],
        "rows": [],
    }


def detect_permission_sensitive_request(message: str, context, intent_result: IntentResult | None = None) -> bool:
    text = (message or "").strip()
    if not text:
        return False

    if context.role == "employee":
        if any(pattern in text for pattern in PERMISSION_SENSITIVE_PATTERNS):
            return True
        if intent_result and intent_result.intent == "dashboard_summary":
            return True
        if intent_result and intent_result.filters.employee_name and intent_result.filters.employee_name != context.employee_name:
            return True

    if intent_result and intent_result.intent == "dashboard_summary":
        if context.role == "employee":
            return True
        allowed_departments = get_allowed_dashboard_departments(context)
        if allowed_departments and intent_result.filters.department_name:
            return intent_result.filters.department_name not in allowed_departments

    return False


def _normalize_relative_dates(filters: QueryFilters, message: str) -> QueryFilters:
    text = message or ""
    today = date.today()
    if "最近 24 小时" in text or "最近24小时" in text:
        filters.date_start = (today - timedelta(days=1)).isoformat()
        filters.date_end = (today + timedelta(days=1)).isoformat()
    elif "今天" in text:
        filters.date_start = today.isoformat()
        filters.date_end = (today + timedelta(days=1)).isoformat()
    elif "最近一周" in text or "近一周" in text:
        filters.date_start = (today - timedelta(days=7)).isoformat()
        filters.date_end = (today + timedelta(days=1)).isoformat()
    elif "本周" in text:
        week_start = today - timedelta(days=today.weekday())
        filters.date_start = week_start.isoformat()
        filters.date_end = (week_start + timedelta(days=7)).isoformat()
    elif "本月" in text:
        month_start = today.replace(day=1)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        filters.date_start = month_start.isoformat()
        filters.date_end = month_end.isoformat()
    elif "最近" in text and not filters.date_start:
        filters.date_start = (today - timedelta(days=7)).isoformat()
        filters.date_end = (today + timedelta(days=1)).isoformat()
    return filters


def _extract_known_ids(filters: QueryFilters, message: str) -> QueryFilters:
    if not filters.order_id:
        match = re.search(r"\bORD\d{3}\b", message.upper())
        if match:
            filters.order_id = match.group(0)
    if not filters.complaint_id:
        match = re.search(r"\b(?:CMP[A-Z0-9]+|C\d{3})\b", message.upper())
        if match:
            filters.complaint_id = match.group(0)
    if not filters.ticket_id:
        match = re.search(r"\b(?:TCK[A-Z0-9]+|T\d{3})\b", message.upper())
        if match:
            filters.ticket_id = match.group(0)
    if not filters.passenger_id:
        match = re.search(r"\b(?:PSG\d{3}|P\d{3})\b", message.upper())
        if match:
            filters.passenger_id = match.group(0)
    if not filters.driver_id:
        match = re.search(r"\b(?:DRV\d{3}|D\d{3})\b", message.upper())
        if match:
            filters.driver_id = match.group(0)
    if not filters.employee_id:
        match = re.search(r"\b(?:EMP\d{3}|E\d{3})\b", message.upper())
        if match:
            filters.employee_id = match.group(0)
    return filters


def normalize_filters(filters: QueryFilters, message: str, derive_query_kind: bool = True) -> QueryFilters:
    text = message or ""
    filters = _extract_known_ids(filters, text)
    filters = _normalize_relative_dates(filters, text)

    if filters.department_name:
        normalized_department = DEPARTMENT_ALIAS_MAP.get(filters.department_name)
        if normalized_department:
            filters.department_name = normalized_department

    if ("P1" in text or "高优先级" in text) and not filters.priority_level:
        filters.priority_level = "P1"
    if ("P2" in text or "中高优先级" in text) and not filters.priority_level:
        filters.priority_level = "P2"
    if "未关闭" in text and not filters.ticket_status:
        filters.ticket_status = "未关闭"
    if "已关闭" in text and not filters.ticket_status:
        filters.ticket_status = "已关闭"
    if "已升级" in text and not filters.ticket_status:
        filters.ticket_status = "已升级"
    if "待分派" in text and not filters.ticket_status:
        filters.ticket_status = "待分派"
    if "待反馈" in text and not filters.ticket_status:
        filters.ticket_status = "待反馈"
    if "处理中" in text and not filters.ticket_status:
        filters.ticket_status = "处理中"
    if "已重开" in text and not filters.ticket_status:
        filters.ticket_status = "已重开"
    if "待处理" in text and not filters.complaint_status:
        filters.complaint_status = "待处理"
    if "即将超时" in text:
        filters.query_kind = filters.query_kind or "near_sla"
        filters.is_overdue = False
    elif "超时" in text and filters.is_overdue is None:
        filters.is_overdue = True
    if ("费用争议" in text or "退款" in text or "多收费" in text) and not filters.complaint_type:
        filters.complaint_type = "费用争议"
    if ("服务态度" in text or "态度差" in text or "辱骂" in text or "拒载" in text) and not filters.complaint_type:
        filters.complaint_type = "司机服务"
    if "安全事件" in text and not filters.complaint_type:
        filters.complaint_type = "安全事件"
    if "司机服务" in text and not filters.complaint_type:
        filters.complaint_type = "司机服务"
    if "取消争议" in text and not filters.complaint_type:
        filters.complaint_type = "取消争议"
    if "物品遗失" in text and not filters.complaint_type:
        filters.complaint_type = "物品遗失"
    if "平台异常" in text and not filters.complaint_type:
        filters.complaint_type = "平台异常"
    if "其他问题" in text and not filters.complaint_type:
        filters.complaint_type = "其他问题"
    if not filters.department_name:
        for alias, canonical in DEPARTMENT_ALIAS_MAP.items():
            if alias in text:
                filters.department_name = canonical
                break
    if "客服部" in text and not filters.department_name:
        filters.department_name = "客服部"
    if "财务售后部" in text and not filters.department_name:
        filters.department_name = "财务售后部"
    if "安全部" in text and not filters.department_name:
        filters.department_name = "安全部"
    if "运营部" in text and not filters.department_name:
        filters.department_name = "运营部"
    if "技术支持部" in text and not filters.department_name:
        filters.department_name = "技术支持部"
    if "系统管理部" in text and not filters.department_name:
        filters.department_name = "系统管理部"
    if "U1" in text and not filters.urgency_level:
        filters.urgency_level = "U1"
    elif "U2" in text and not filters.urgency_level:
        filters.urgency_level = "U2"
    elif "U3" in text and not filters.urgency_level:
        filters.urgency_level = "U3"
    elif "U4" in text and not filters.urgency_level:
        filters.urgency_level = "U4"
    if "高" in text and "紧急程度" in text and not filters.urgency_level:
        filters.urgency_level = "U1"

    if "已完成" in text and not filters.order_status and "订单" in text:
        filters.order_status = "已完成"
    elif "已取消" in text and not filters.order_status:
        filters.order_status = "已取消"
    elif "进行中" in text and not filters.order_status:
        filters.order_status = "进行中"
    elif "异常" in text and not filters.order_status and "订单" in text:
        filters.order_status = "异常"

    amount_match = re.search(r"(?:大于|超过|高于)\s*(\d+(?:\.\d+)?)\s*元", text)
    if amount_match and filters.min_amount is None:
        filters.min_amount = float(amount_match.group(1))
    amount_match = re.search(r"(?:小于|低于)\s*(\d+(?:\.\d+)?)\s*元", text)
    if amount_match and filters.max_amount is None:
        filters.max_amount = float(amount_match.group(1))
    score_match = re.search(r"(?:评分|满意度|星).*?(?:低于|小于)\s*([1-5])", text)
    if not score_match:
        score_match = re.search(r"(?:低于|小于)\s*([1-5])\s*(?:分|星)", text)
    if score_match and filters.score_below is None:
        filters.score_below = int(score_match.group(1))
    sla_hours_match = re.search(r"(?:SLA|剩余).{0,8}?(?:小于|低于|不超过)\s*(\d{1,3})\s*小时", text)
    if sla_hours_match and filters.sla_within_hours is None:
        filters.sla_within_hours = int(sla_hours_match.group(1))

    has_relation_filter = any(
        getattr(filters, name) is not None
        for name in ("has_assignment", "has_action_log", "has_escalation", "has_feedback")
    )
    if derive_query_kind and not filters.query_kind:
        filters.query_kind = _keyword_derived_query_kind(text, has_relation_filter)
    if ("主管层" in text or "主管复核" in text) and "升级" in text and not filters.to_level:
        filters.to_level = "主管复核"

    return canonicalize_query_filters(filters)


def _keyword_derived_query_kind(text: str, has_relation_filter: bool) -> str | None:
    """关键词 → query_kind 兜底推导。仅当 LLM 未给出 query_kind 时使用，绝不覆盖模型判断。"""
    if "默认部门" in text or "默认 SLA" in text or "默认SLA" in text:
        return "complaint_type_rules"
    if "接单数量" in text or "完成率" in text:
        return "driver_order_stats"
    if "是否产生过投诉" in text or "有没有投诉" in text:
        return "order_complaint_check"
    if "生命周期" in text:
        return "ticket_lifecycle"
    if not has_relation_filter and "完整" not in text and (
        "分派历史" in text or "分派记录" in text or ("分派" in text and any(token in text for token in ("最近", "今天")))
    ):
        return "assignment_history"
    if not has_relation_filter and "完整" not in text and ("升级原因" in text or "升级记录" in text or "被升级过" in text):
        return "escalation_history"
    if ("主管层" in text or "主管复核" in text) and "升级" in text:
        return "escalation_history"
    if not has_relation_filter and "完整" not in text and ("处理日志" in text or "历史操作时间线" in text or "操作日志" in text):
        return "action_log_history"
    if "反馈与投诉类型" in text or "反馈统计关系" in text:
        return "feedback_type_stats"
    if any(token in text for token in ("健康状况", "风险评级", "超时工单比例", "平均处理时长")):
        return "department_health"
    if any(token in text for token in ("综合绩效排名", "平均关闭时间", "超时率")) and "满意度" in text:
        return "department_performance"
    if (
        ("评分" in text or "满意度" in text or "反馈" in text)
        and ("高风险未闭环工单" in text or "平均处理日志数量" in text or "是否发生过升级" in text)
    ):
        return "low_feedback_risk"
    if all(token in text for token in ("即将超时", "已超时", "已升级")) and ("风险分布" in text or "按部门汇总" in text):
        return "sla_risk_scan"
    if all(token in text for token in ("投诉类型", "平均处理时长", "平均满意度", "升级率")):
        return "complaint_type_quality"
    if "司机" in text and "被投诉次数" in text and "满意度" in text:
        return "driver_service_risk"
    if any(token in text for token in ("员工维度异常", "摸鱼", "过载", "高超时率")):
        return "employee_efficiency_anomaly"
    if any(token in text for token in ("一个投诉生成多个工单", "工单缺失投诉来源", "投诉未生成工单", "一致性问题报告")):
        return "conversion_consistency_audit"
    if any(token in text for token in ("升级是否有效", "升级最频繁", "升级后是否提升满意度")):
        return "escalation_effectiveness"
    if "客服部门" in text and "平均负载" in text and ("高负载员工" in text or "负载不均" in text):
        return "customer_service_balance"
    if "Top 10%" in text and "订单" in text and ("投诉率" in text or "高价值用户体验风险" in text):
        return "high_value_order_risk"
    if "risk_score" in text.lower() or ("风险评分" in text and "Top 50" in text):
        return "ticket_risk_scoring"
    if any(token in text for token in ("系统日报", "健康等级", "关闭率 vs 新增率", "满意度分布变化")):
        return "system_health_report"
    return None


def _heuristic_feedback_low_score(message: str) -> bool:
    text = message or ""
    return (
        "低满意度" in text
        or "满意度低于 3" in text
        or "满意度低于3" in text
        or "低于 3 分" in text
        or "评分低于 3" in text
        or "评分低于3" in text
    )


def _build_query_clarification(text: str, intent_result: IntentResult) -> dict | None:
    filters = intent_result.filters
    if "某员工" in text and filters.query_kind == "assignment_history" and not (filters.employee_id or filters.employee_name):
        return {
            "ok": True,
            "answer": "请补充员工编号或姓名，例如 E003、EMP003 或员工姓名，我再查询对应员工最近分派的工单。",
            "intent": intent_result.intent,
            "data_count": 0,
            "risk_flags": [],
            "rows": [],
            "commands": [],
            "executed_steps": [],
        }
    if (
        "某工单" in text
        and filters.query_kind in {"ticket_lifecycle", "escalation_history", "action_log_history"}
        and not filters.ticket_id
    ):
        return {
            "ok": True,
            "answer": "请补充工单编号，例如 T001 或真实工单编号，我再为你查询对应的生命周期、升级原因或处理时间线。",
            "intent": intent_result.intent,
            "data_count": 0,
            "risk_flags": [],
            "rows": [],
            "commands": [],
            "executed_steps": [],
        }
    return None

    if intent == "dashboard_summary" and "due_within_6h_count" in rows[0]:
        total_due = sum(row.get("due_within_6h_count", 0) for row in rows)
        total_overdue = sum(row.get("overdue_open_count", 0) for row in rows)
        total_overdue_escalated = sum(row.get("overdue_escalated_count", 0) for row in rows)
        top = rows[0]
        return (
            f"当前已完成全局 SLA 风险扫描：6 小时内即将超时 {total_due} 条，"
            f"已超时未关闭 {total_overdue} 条，其中超时且已升级 {total_overdue_escalated} 条。"
            f"风险最集中的部门是 {top.get('department_name', '-')}"
            f"，其超时未关闭工单 {top.get('overdue_open_count', 0)} 条。"
        )

    if intent == "dashboard_summary" and "difficulty_score" in rows[0]:
        top3 = [row.get("complaint_type", "-") for row in rows[:3]]
        return (
            f"当前已完成投诉驱动质量分析，共覆盖 {len(rows)} 类投诉。"
            f"综合处理难度最高的前三类投诉分别是：{'、'.join(top3)}。"
            f"其中 {rows[0].get('complaint_type', '-')} 平均处理时长 {rows[0].get('avg_handle_hours', 0)} 小时，"
            f"平均满意度 {rows[0].get('avg_satisfaction_score', 0)} 分，升级率 {rows[0].get('escalation_rate', 0)}%。"
        )

    if intent == "dashboard_summary" and rows[0].get("row_type") == "escalation_effectiveness_summary":
        first = rows[0]
        return (
            f"当前已完成升级路径效率分析。升级平均耗时 {first.get('avg_escalation_hours', 0)} 小时，"
            f"升级工单平均满意度 {first.get('avg_escalated_satisfaction', 0)} 分，"
            f"未升级工单平均满意度 {first.get('avg_non_escalated_satisfaction', 0)} 分。"
            f"升级最频繁的部门是 {first.get('most_frequent_department', '-')}"
            f"，整体判断升级{'有效' if first.get('is_effective') else '效果一般'}。"
        )

    if intent == "dashboard_summary" and rows[0].get("row_type") == "cs_balance_summary":
        first = rows[0]
        return (
            f"当前已完成客服部门负载均衡分析。客服部未关闭工单 {first.get('open_ticket_count', 0)} 条，"
            f"人均负载 {first.get('avg_employee_load', 0)} 条，Top 10% 高负载员工人数 {first.get('top_load_threshold_count', 0)}。"
            f"系统判断当前客服负载{'存在严重不均' if first.get('severe_imbalance') else '整体较均衡'}。"
        )

    if intent == "dashboard_summary" and rows[0].get("row_type") == "system_health_summary":
        first = rows[0]
        return (
            f"当前已生成系统级运营健康报告：今日新增投诉 {first.get('new_complaints_today', 0)} 条，"
            f"新增工单 {first.get('new_tickets_today', 0)} 条，关闭 {first.get('closed_today', 0)} 条，关闭率 {first.get('close_rate_pct', 0)}%。"
            f"近 7 天 SLA 超时率为 {first.get('sla_overdue_rate_current_7d', 0)}%，"
            f"系统健康等级为 {first.get('health_level', 'Healthy')}。"
        )

    if intent == "dashboard_summary" and "anomaly_type" in rows[0]:
        return (
            f"当前已识别出 {len(rows)} 名工单处理效率异常员工。"
            f"最显著的异常是 {rows[0].get('employee_name', '-')}"
            f"，异常类型为 {rows[0].get('anomaly_type', '-')}"
            f"，其工单量 {rows[0].get('processed_ticket_count', 0)}，SLA 超时率 {rows[0].get('overdue_rate', 0)}%。"
        )

    if intent == "complaint_query" and rows[0].get("row_type") == "consistency_summary":
        first = rows[0]
        total_issues = (
            first.get("multi_ticket_complaint_count", 0)
            + first.get("orphan_ticket_count", 0)
            + first.get("complaints_without_ticket_count", 0)
        )
        return (
            f"当前已完成投诉-工单转化链路一致性检查，共发现 {total_issues} 项异常。"
            f"其中一个投诉生成多个工单 {first.get('multi_ticket_complaint_count', 0)} 项，"
            f"工单缺失投诉来源 {first.get('orphan_ticket_count', 0)} 项，"
            f"投诉未生成工单 {first.get('complaints_without_ticket_count', 0)} 项。"
        )

    if intent == "order_query" and "driver_complaint_count" in rows[0]:
        return (
            f"当前已完成司机服务质量联动分析，共识别出 {len(rows)} 名高风险司机。"
            f"其中风险最高的是 {rows[0].get('driver_name', '-')}"
            f"，被投诉 {rows[0].get('driver_complaint_count', 0)} 次，"
            f"平均满意度 {rows[0].get('avg_satisfaction_score', 0)} 分。"
        )

    if intent == "order_query" and rows[0].get("row_type") == "high_value_order_summary":
        first = rows[0]
        return (
            f"当前已完成高价值订单投诉影响分析。金额 Top 10% 订单共 {first.get('top_order_count', 0)} 单，"
            f"投诉率 {first.get('complaint_rate', 0)}%，SLA 超时率 {first.get('sla_overdue_rate', 0)}%，"
            f"平均满意度 {first.get('avg_satisfaction_score', 0)} 分，整体风险评级为 {first.get('risk_rating', '中')}。"
        )

    if intent == "risk_query" and "risk_score" in rows[0]:
        return (
            f"当前已完成多维风险评分，输出前 {len(rows)} 条高风险工单。"
            f"风险最高的工单是 {rows[0].get('ticket_id', '-')}"
            f"，风险分数 {rows[0].get('risk_score', 0)}，"
            f"SLA 剩余时间 {rows[0].get('sla_remaining_hours', 0)} 小时。"
        )

    return None


def _serialize_for_summary(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json_payload(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("模型未返回结构化内容。")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("模型返回内容中未找到 JSON 对象。")
    return json.loads(match.group(0))


def _build_fallback_answer(intent: str, rows: list[dict], risk_flags: list[str]) -> str:
    if not rows:
        return "当前权限范围内未查询到匹配数据。建议调整工单状态、时间范围、投诉类型或订单编号后重试。"

    first = rows[0]
    if intent == "ticket_query":
        sentence = (
            f"共查询到 {len(rows)} 条工单。首条工单为 {first.get('ticket_id', '-')}"
            f"，状态为 {first.get('ticket_status', '-')}"
            f"，优先级为 {first.get('priority_level', '-')}。"
        )
        if risk_flags:
            sentence += f" 风险提示：{'、'.join(risk_flags)}。"
        return sentence
    if intent == "dashboard_summary":
        return f"已生成部门统计摘要，共 {len(rows)} 条记录，可重点关注超时和处理中数量较高的部门。"
    if intent == "feedback_query":
        return f"共查询到 {len(rows)} 条反馈记录。建议优先关注低满意度和待复核工单。"
    if intent == "action_suggestion":
        return first.get("suggested_actions", "建议进入工单详情页继续处理。")
    return f"共查询到 {len(rows)} 条结果。"


def _build_verified_answer(message: str, intent: str, rows: list[dict], risk_flags: list[str]) -> str | None:
    text = message or ""
    if not rows:
        return None

    if intent == "dashboard_summary" and "risk_rating" in rows[0]:
        high_risk = [row for row in rows if row.get("risk_rating") == "高"]
        top = rows[0]
        return (
            f"当前已完成各部门工单健康状况分析，共覆盖 {len(rows)} 个部门。"
            f"风险最高的是 {top.get('department_name', '-')}"
            f"，未关闭工单 {top.get('open_ticket_count', 0)} 条，超时比例 {top.get('overdue_ratio', 0)}%，"
            f"风险评级为 {top.get('risk_rating', '-')}"
            f"。其中高风险部门共 {len(high_risk)} 个。"
        )

    if intent == "dashboard_summary" and "performance_rank" in rows[0]:
        best = rows[0]
        worst = rows[-1]
        return (
            f"当前已完成部门综合绩效排名，共覆盖 {len(rows)} 个部门。"
            f"排名第 1 的是 {best.get('department_name', '-')}"
            f"，综合得分 {best.get('performance_score', 0)}；"
            f"排名末位的是 {worst.get('department_name', '-')}"
            f"，综合得分 {worst.get('performance_score', 0)}。"
        )

    if intent == "order_query":
        first = rows[0]
        if "完成率" in text and "completion_rate" in first:
            return (
                f"司机 {first.get('driver_name', first.get('driver_id', '-'))} 当前可见范围内共有 "
                f"{first.get('total_orders', 0)} 单，已完成 {first.get('completed_orders', 0)} 单，完成率 "
                f"{first.get('completion_rate', 0)}%。"
            )
        if ("是否产生过投诉" in text or "有没有投诉" in text) and "has_complaint" in first:
            return (
                f"订单 {first.get('order_id', '-')} "
                f"{'已产生投诉' if first.get('has_complaint') else '尚未产生投诉'}，"
                f"共 {first.get('complaint_count', 0)} 条投诉记录。"
            )
        if any(token in text for token in ("多少", "几", "数量", "总数")):
            return f"当前查询到 {len(rows)} 条订单记录。"

    if intent == "dashboard_summary" and any(token in text for token in ("多少", "几", "数量", "总数")):
        first = rows[0]
        department_name = first.get("department_name", "该部门")
        if "超时" in text and "overdue_tickets" in first:
            count = first.get("overdue_tickets", 0)
            extra = f" 风险提示：{'、'.join(risk_flags)}。" if risk_flags else ""
            return f"{department_name}当前共有 {count} 条超时工单。{extra}".strip()
        if "待反馈" in text and "pending_feedback_tickets" in first:
            count = first.get("pending_feedback_tickets", 0)
            return f"{department_name}当前共有 {count} 条待反馈工单。"
        if "已升级" in text and "escalated_tickets" in first:
            count = first.get("escalated_tickets", 0)
            extra = f" 风险提示：{'、'.join(risk_flags)}。" if risk_flags else ""
            return f"{department_name}当前共有 {count} 条已升级工单。{extra}".strip()
        if "已重开" in text and "reopened_tickets" in first:
            count = first.get("reopened_tickets", 0)
            extra = f" 风险提示：{'、'.join(risk_flags)}。" if risk_flags else ""
            return f"{department_name}当前共有 {count} 条已重开工单。{extra}".strip()
        if "待分派" in text and "unassigned_tickets" in first:
            count = first.get("unassigned_tickets", 0)
            return f"{department_name}当前共有 {count} 条待分派工单。"
        if "处理中" in text and "processing_tickets" in first:
            count = first.get("processing_tickets", 0)
            extra = f" 风险提示：{'、'.join(risk_flags)}。" if risk_flags else ""
            return f"{department_name}当前共有 {count} 条处理中工单。{extra}".strip()
        if "已关闭" in text and "closed_tickets" in first:
            count = first.get("closed_tickets", 0)
            return f"{department_name}当前共有 {count} 条已关闭工单。"
        if "工单" in text and "total_tickets" in first:
            count = first.get("total_tickets", 0)
            return f"{department_name}当前共有 {count} 条工单。"

    if intent in {"ticket_query", "risk_query", "complaint_query", "feedback_query"} and any(
        token in text for token in ("多少", "几", "数量", "总数")
    ):
        noun = {
            "ticket_query": "工单",
            "risk_query": "风险工单",
            "complaint_query": "投诉",
            "feedback_query": "反馈记录",
        }.get(intent, "记录")
        extra = f" 风险提示：{'、'.join(risk_flags)}。" if risk_flags else ""
        return f"当前查询到 {len(rows)} 条{noun}。{extra}".strip()

    if intent == "complaint_query" and "default_department_name" in rows[0]:
        first = rows[0]
        return (
            f"投诉类型 {first.get('complaint_type', '-')} 默认流转到 {first.get('default_department_name', '-')}，"
            f"默认优先级 {first.get('default_priority_level', '-')}，SLA 为 {first.get('default_sla_hours', '-')} 小时。"
        )

    if intent == "feedback_query" and "avg_satisfaction_score" in rows[0]:
        first = rows[0]
        return (
            f"当前已按投诉类型汇总反馈结果，共 {len(rows)} 类。"
            f"其中 {first.get('complaint_type', '-')} 有 {first.get('feedback_count', 0)} 条反馈，"
            f"平均满意度 {first.get('avg_satisfaction_score', 0)} 分。"
        )

    if intent == "feedback_query" and rows[0].get("row_type") == "analysis_summary":
        first = rows[0]
        return (
            f"当前共发现 {first.get('low_feedback_ticket_count', 0)} 条低分反馈工单，"
            f"其中未闭环高风险工单 {first.get('high_risk_unclosed_count', 0)} 条，"
            f"平均处理日志数量 {first.get('avg_action_log_count', 0)} 条。"
            f"投诉类型分布为 {first.get('complaint_type_distribution', '-')}"
            f"。"
        )

    return None


def _call_intent_model(message: str, context) -> IntentResult:
    llm = get_deepseek_llm(temperature=0.0)
    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        try:
            structured_llm = llm.with_structured_output(IntentResult)
            result = structured_llm.invoke(
                [
                    SystemMessage(content=get_intent_system_prompt(context)),
                    HumanMessage(content=message),
                ]
            )
            if isinstance(result, IntentResult):
                return result
            return IntentResult.model_validate(result)
        except Exception:
            pass

    result = call_deepseek_chat(
        [
            {
                "role": "system",
                "content": get_intent_system_prompt(context)
                + "\n请只返回 JSON，字段必须包含 intent、filters、reason。",
            },
            {"role": "user", "content": message},
        ],
        temperature=0.0,
    )
    if not result["ok"]:
        raise RuntimeError(result["message"])

    payload = _extract_json_payload(result["content"])
    return IntentResult.model_validate(payload)


def _should_attempt_nested_query(message: str) -> bool:
    text = (message or "").strip()
    return any(token in text for token in NESTED_CONNECTORS)


def _call_plan_model(message: str, context) -> QueryPlanResult:
    llm = get_deepseek_llm(temperature=0.0)
    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        try:
            structured_llm = llm.with_structured_output(QueryPlanResult)
            result = structured_llm.invoke(
                [
                    SystemMessage(content=get_plan_system_prompt(context)),
                    HumanMessage(content=message),
                ]
            )
            if isinstance(result, QueryPlanResult):
                return result
            return QueryPlanResult.model_validate(result)
        except Exception:
            pass

    result = call_deepseek_chat(
        [
            {
                "role": "system",
                "content": get_plan_system_prompt(context)
                + "\n请只返回 JSON，字段必须包含 steps 和 reason。",
            },
            {"role": "user", "content": message},
        ],
        temperature=0.0,
    )
    if not result["ok"]:
        raise RuntimeError(result["message"])

    payload = _extract_json_payload(result["content"])
    return QueryPlanResult.model_validate(payload)


def _call_analysis_intent_model(message: str, context) -> IntentResult:
    llm = get_deepseek_llm(temperature=0.0)
    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        try:
            structured_llm = llm.with_structured_output(IntentResult)
            result = structured_llm.invoke(
                [
                    SystemMessage(content=get_analysis_intent_system_prompt(context)),
                    HumanMessage(content=message),
                ]
            )
            if isinstance(result, IntentResult):
                return result
            return IntentResult.model_validate(result)
        except Exception:
            pass

    result = call_deepseek_chat(
        [
            {
                "role": "system",
                "content": get_analysis_intent_system_prompt(context)
                + "\n请只返回 JSON，字段必须包含 intent、filters、reason。",
            },
            {"role": "user", "content": message},
        ],
        temperature=0.0,
    )
    if not result["ok"]:
        raise RuntimeError(result["message"])

    payload = _extract_json_payload(result["content"])
    return IntentResult.model_validate(payload)


def _apply_analysis_override(message: str, context, primary_intent: IntentResult) -> IntentResult:
    try:
        analysis_intent = _call_analysis_intent_model(message, context)
    except Exception:
        return primary_intent

    analysis_intent.filters = normalize_filters(analysis_intent.filters, message, derive_query_kind=False)
    query_kind = analysis_intent.filters.query_kind
    if query_kind not in ANALYSIS_QUERY_KINDS:
        return primary_intent
    if analysis_intent.intent == "unsupported":
        return primary_intent
    return analysis_intent


def _rule_based_intent(message: str, context, base: IntentResult | None = None) -> IntentResult:
    text = (message or "").strip()
    lower = text.lower()
    filters = base.filters if base else QueryFilters()
    filters = normalize_filters(filters, text)
    reason_parts = ["使用规则兜底识别意图"]

    has_ticket_id = bool(filters.ticket_id)
    has_order_id = bool(filters.order_id)
    has_ticket_word = "工单" in text or "ticket" in lower
    has_order_word = "订单" in text or "order" in lower
    has_complaint_word = "投诉" in text or "complaint" in lower
    has_feedback_word = "反馈" in text or "满意度" in text or "评价" in text
    has_action_word = "建议" in text or "怎么处理" in text or "下一步" in text or "如何处理" in text
    has_dashboard_word = any(keyword in text for keyword in DASHBOARD_HINTS)
    has_risk_word = any(keyword in text for keyword in RISK_HINTS)
    has_detail_word = any(keyword in text for keyword in DETAIL_HINTS)
    has_department_alias = any(alias in text for alias in DEPARTMENT_ALIAS_MAP)
    asks_for_count = any(token in text for token in ("多少", "几", "数量", "总数"))

    if filters.query_kind == "driver_order_stats" or filters.driver_id:
        if "接单数量" in text or "完成率" in text:
            return IntentResult(intent="order_query", filters=filters, reason="规则识别为司机订单统计")

    if filters.query_kind == "near_sla":
        return IntentResult(intent="ticket_query", filters=filters, reason="规则识别为即将超时工单查询")

    if filters.query_kind == "order_complaint_check":
        return IntentResult(intent="order_query", filters=filters, reason="规则识别为订单是否投诉查询")

    if filters.query_kind == "complaint_type_rules":
        return IntentResult(intent="complaint_query", filters=filters, reason="规则识别为投诉类型规则查询")

    if filters.query_kind in {"department_health", "department_performance"}:
        return IntentResult(intent="dashboard_summary", filters=filters, reason="规则识别为部门分析统计查询")

    if filters.query_kind == "low_feedback_risk":
        return IntentResult(intent="feedback_query", filters=filters, reason="规则识别为低分反馈风险分析")

    analysis_intent_map = {
        "sla_risk_scan": "dashboard_summary",
        "complaint_type_quality": "dashboard_summary",
        "driver_service_risk": "order_query",
        "employee_efficiency_anomaly": "dashboard_summary",
        "conversion_consistency_audit": "complaint_query",
        "escalation_effectiveness": "dashboard_summary",
        "customer_service_balance": "dashboard_summary",
        "high_value_order_risk": "order_query",
        "ticket_risk_scoring": "risk_query",
        "system_health_report": "dashboard_summary",
    }
    if filters.query_kind in analysis_intent_map:
        return IntentResult(intent=analysis_intent_map[filters.query_kind], filters=filters, reason="规则识别为高级分析查询")

    if filters.query_kind in {"ticket_lifecycle", "assignment_history", "escalation_history", "action_log_history"}:
        return IntentResult(intent="ticket_query", filters=filters, reason="规则识别为工单过程记录查询")

    if filters.query_kind == "feedback_type_stats":
        return IntentResult(intent="feedback_query", filters=filters, reason="规则识别为反馈统计查询")

    if has_action_word and has_ticket_id:
        return IntentResult(intent="action_suggestion", filters=filters, reason="规则识别为工单处理建议")

    if has_ticket_id and (has_detail_word or "当前状态" in text or "负责人" in text):
        return IntentResult(intent="ticket_detail", filters=filters, reason="规则识别为工单详情查询")

    if has_dashboard_word and ("部门" in text or "类型" in text or "优先级" in text or "投诉量" in text or has_department_alias):
        if "超时" in text and filters.is_overdue is None:
            filters.is_overdue = True
        return IntentResult(intent="dashboard_summary", filters=filters, reason="规则识别为统计汇总查询")

    if asks_for_count and filters.department_name and "超时" in text:
        filters.is_overdue = True
        return IntentResult(intent="dashboard_summary", filters=filters, reason="规则识别为按部门统计超时工单数量")

    if asks_for_count and filters.department_name and any(
        token in text for token in ("待反馈", "已升级", "已重开", "待分派", "处理中", "工单")
    ):
        return IntentResult(intent="dashboard_summary", filters=filters, reason="规则识别为按部门统计工单数量")

    if has_feedback_word:
        return IntentResult(intent="feedback_query", filters=filters, reason="规则识别为反馈查询")

    if has_risk_word and (has_ticket_word or has_order_id or has_ticket_id or "部门" in text or has_department_alias):
        return IntentResult(intent="risk_query", filters=filters, reason="规则识别为风险工单查询")

    if has_order_id and (has_ticket_word or "进展" in text or "负责人" in text or "SLA" in text):
        return IntentResult(intent="ticket_query", filters=filters, reason="规则识别为按订单追踪工单")

    if has_complaint_word and not has_ticket_word and not has_feedback_word:
        return IntentResult(intent="complaint_query", filters=filters, reason="规则识别为投诉查询")

    if has_order_word and not has_ticket_word and not has_complaint_word:
        return IntentResult(intent="order_query", filters=filters, reason="规则识别为订单查询")

    if has_ticket_word or has_ticket_id or "P1" in text or "未关闭" in text or "负责人" in text or "SLA" in text or "待办" in text:
        return IntentResult(intent="ticket_query", filters=filters, reason="规则识别为工单查询")

    if "我的" in text and context.role == "employee":
        return IntentResult(intent="ticket_query", filters=filters, reason="规则识别为员工个人待办查询")

    reason_parts.append("未命中业务语义")
    return IntentResult(intent="unsupported", filters=filters, reason="；".join(reason_parts))


def _rule_based_plan(message: str, context, base: QueryPlanResult | None = None) -> QueryPlanResult:
    del base
    single = _rule_based_intent(message, context)
    if single.filters.query_kind in {"department_health", "department_performance", "low_feedback_risk"}:
        return QueryPlanResult(
            steps=[
                PlanStep(
                    intent=single.intent,
                    filters=single.filters,
                    step_title="执行主查询",
                    depends_on_previous=False,
                    reference_field="none",
                    reason=single.reason,
                )
            ],
            reason="规则识别为单步分析查询",
        )
    if not _should_attempt_nested_query(message):
        return QueryPlanResult(
            steps=[
                PlanStep(
                    intent=single.intent,
                    filters=single.filters,
                    step_title="执行主查询",
                    depends_on_previous=False,
                    reference_field="none",
                    reason=single.reason,
                )
            ],
            reason="规则识别为单步查询",
        )

    steps: list[PlanStep] = []
    text = (message or "").strip()

    if "部门" in text and "超时" in text and ("并列出" in text or "再" in text or "该部门" in text):
        step1_filters = normalize_filters(QueryFilters(is_overdue=True), text)
        step2_filters = normalize_filters(QueryFilters(), text)
        steps.append(
            PlanStep(
                intent="dashboard_summary",
                filters=step1_filters,
                step_title="先找出超时工单较多的部门",
                depends_on_previous=False,
                reference_field="none",
                reason="规则识别为先做部门统计",
            )
        )
        steps.append(
            PlanStep(
                intent="ticket_query",
                filters=step2_filters,
                step_title="再列出该部门的相关工单",
                depends_on_previous=True,
                reference_field="department_name",
                reason="规则识别为基于上一步部门结果继续查工单",
            )
        )
        return QueryPlanResult(steps=steps, reason="规则识别为部门到工单的嵌套查询")

    if ("低满意度" in text or "反馈" in text) and ("相关工单" in text or "再查看工单" in text or "再查工单" in text):
        step1_filters = normalize_filters(QueryFilters(), text)
        step2_filters = normalize_filters(QueryFilters(), text)
        steps.append(
            PlanStep(
                intent="feedback_query",
                filters=step1_filters,
                step_title="先查询低满意度反馈",
                depends_on_previous=False,
                reference_field="none",
                reason="规则识别为先查反馈",
            )
        )
        steps.append(
            PlanStep(
                intent="ticket_detail",
                filters=step2_filters,
                step_title="再查看相关工单详情",
                depends_on_previous=True,
                reference_field="ticket_id",
                reason="规则识别为根据反馈结果追踪工单",
            )
        )
        return QueryPlanResult(steps=steps, reason="规则识别为反馈到工单的嵌套查询")

    single = _rule_based_intent(message, context)
    return QueryPlanResult(
        steps=[
            PlanStep(
                intent=single.intent,
                filters=single.filters,
                step_title="执行主查询",
                depends_on_previous=False,
                reference_field="none",
                reason=single.reason,
            )
        ],
        reason="规则降级为单步查询",
    )


def _repair_intent_result(intent_result: IntentResult, message: str, context) -> IntentResult:
    heuristic = _rule_based_intent(message, context, base=intent_result)
    text = message or ""
    asks_for_count = any(token in text for token in ("多少", "几", "数量", "总数"))
    has_feedback_signal = any(token in text for token in ("反馈", "满意度", "评价", "低满意度", "评分低于"))
    has_risk_signal = any(token in text for token in ("P1", "高优先级", "超时", "安全事件", "已升级", "待反馈"))

    if heuristic.filters.query_kind and intent_result.intent == "unsupported":
        return heuristic

    if intent_result.intent in {"unsupported"}:
        return heuristic

    if intent_result.intent == "permission_sensitive" and heuristic.intent not in {"unsupported", "permission_sensitive"}:
        return heuristic

    if heuristic.filters.query_kind and heuristic.intent != intent_result.intent and heuristic.intent in {
        "order_query",
        "complaint_query",
        "feedback_query",
        "ticket_query",
    }:
        return heuristic

    if (
        heuristic.intent == "dashboard_summary"
        and intent_result.intent in {"unsupported", "complaint_query", "ticket_query"}
        and asks_for_count
        and (heuristic.filters.department_name or "部门" in text or "统计" in text or "汇总" in text)
    ):
        return heuristic

    if heuristic.intent == "feedback_query" and intent_result.intent in {"unsupported", "ticket_query"} and has_feedback_signal:
        return heuristic

    if heuristic.intent == "risk_query" and intent_result.intent in {"unsupported", "ticket_query"} and has_risk_signal:
        return heuristic

    if intent_result.intent == "order_query" and ("工单" in message or "投诉和工单" in message or "进展" in message):
        return heuristic

    if intent_result.intent == "complaint_query" and ("负责人" in message or "SLA" in message or "工单状态" in message):
        return heuristic

    if intent_result.intent == "ticket_query" and intent_result.filters.ticket_id and any(
        keyword in message for keyword in DETAIL_HINTS
    ):
        return IntentResult(
            intent="ticket_detail",
            filters=intent_result.filters,
            reason=intent_result.reason or "根据工单编号和详情关键词提升为工单详情查询",
        )

    return intent_result


def _repair_plan_result(plan_result: QueryPlanResult, message: str, context) -> QueryPlanResult:
    heuristic = _rule_based_plan(message, context, base=plan_result)
    if not plan_result.steps:
        return heuristic
    if len(plan_result.steps) == 1 and _should_attempt_nested_query(message) and len(heuristic.steps) > 1:
        return heuristic
    repaired_steps: list[PlanStep] = []
    for step in plan_result.steps[:3]:
        normalized_filters = normalize_filters(step.filters, message, derive_query_kind=False)
        repaired_steps.append(
            PlanStep(
                intent=step.intent,
                filters=normalized_filters,
                step_title=step.step_title or f"执行 {step.intent}",
                depends_on_previous=step.depends_on_previous,
                reference_field=step.reference_field,
                reason=step.reason,
            )
        )
    return QueryPlanResult(steps=repaired_steps, reason=plan_result.reason or heuristic.reason)


def _call_summary_model(
    message: str,
    intent: str,
    context,
    rows: list[dict],
    risk_flags: list[str],
    data_count: int,
    commands: list[dict] | None = None,
) -> str:
    payload = {
        "question": message,
        "intent": intent,
        "data_count": data_count,
        "risk_flags": risk_flags,
        "commands": commands or [],
        "rows": rows[:10],
    }
    result = call_deepseek_chat(
        [
            {"role": "system", "content": get_summary_system_prompt(context)},
            {"role": "user", "content": _serialize_for_summary(payload)},
        ],
        temperature=0.2,
    )
    if not result["ok"]:
        return _build_fallback_answer(intent, rows, risk_flags)
    answer = result["content"].strip()
    return answer or _build_fallback_answer(intent, rows, risk_flags)


def _dispatch_intent(intent_result: IntentResult, context, message: str) -> dict:
    filters = intent_result.filters
    intent = intent_result.intent

    if intent == "ticket_query":
        if filters.query_kind == "ticket_lifecycle":
            return query_ticket_lifecycle(context, filters)
        if filters.query_kind == "assignment_history":
            return query_assignments(context, filters)
        if filters.query_kind == "escalation_history":
            return query_escalations(context, filters)
        if filters.query_kind == "action_log_history":
            return query_action_logs(context, filters)
        return query_tickets(context, filters)
    if intent == "ticket_detail":
        return get_ticket_detail(context, filters.ticket_id or "")
    if intent == "complaint_query":
        if filters.query_kind == "complaint_type_rules":
            return query_complaint_type_rules(context, filters)
        return query_complaints(context, filters)
    if intent == "order_query":
        if filters.query_kind == "driver_order_stats":
            return query_driver_order_stats(context, filters)
        return query_orders(context, filters)
    if intent == "dashboard_summary":
        return query_dashboard_summary(context, filters)
    if intent == "feedback_query":
        if filters.query_kind == "feedback_type_stats":
            return query_feedback_stats(context, filters)
        return query_feedback(context, filters, low_score_only=_heuristic_feedback_low_score(message))
    if intent == "risk_query":
        if _heuristic_feedback_low_score(message) or "反馈" in message:
            return query_feedback(context, filters, low_score_only=True)
        return query_risk_tickets(context, filters)
    if intent == "action_suggestion":
        return suggest_ticket_action(context, (filters.ticket_id or "").upper())
    return {"rows": [], "data_count": 0, "risk_flags": []}


def _tool_name_for_intent(intent: str) -> str:
    return {
        "ticket_query": "query_tickets",
        "ticket_detail": "get_ticket_detail",
        "complaint_query": "query_complaints",
        "order_query": "query_orders",
        "dashboard_summary": "query_dashboard_summary",
        "feedback_query": "query_feedback",
        "risk_query": "query_risk_tickets",
        "action_suggestion": "suggest_ticket_action",
    }.get(intent, "unsupported")


def _filters_to_dict(filters: QueryFilters) -> dict:
    return {key: value for key, value in filters.model_dump().items() if value not in (None, "", [], {})}


def _build_command_preview(intent: str, filters: QueryFilters, step_title: str = "") -> dict:
    return {
        "step_title": step_title or f"执行 {intent}",
        "tool": _tool_name_for_intent(intent),
        "intent": intent,
        "filters": _filters_to_dict(filters),
    }


def _extract_reference_value(reference_field: str, previous_result: dict):
    rows = previous_result.get("rows") or []
    if not rows:
        return None
    first = rows[0]
    if reference_field == "department_name":
        return first.get("department_name")
    if reference_field == "complaint_type":
        return first.get("complaint_type")
    if reference_field == "ticket_id":
        return first.get("ticket_id")
    if reference_field == "order_id":
        return first.get("order_id")
    return None


def _inject_reference_filters(step: PlanStep, previous_result: dict) -> PlanStep:
    if not step.depends_on_previous or step.reference_field == "none":
        return step
    reference_value = _extract_reference_value(step.reference_field, previous_result)
    if not reference_value:
        return step

    filters = step.filters.model_copy(deep=True)
    if step.reference_field == "department_name" and not filters.department_name:
        filters.department_name = reference_value
    elif step.reference_field == "complaint_type" and not filters.complaint_type:
        filters.complaint_type = reference_value
    elif step.reference_field == "ticket_id" and not filters.ticket_id:
        filters.ticket_id = reference_value
    elif step.reference_field == "order_id" and not filters.order_id:
        filters.order_id = reference_value

    return PlanStep(
        intent=step.intent,
        filters=filters,
        step_title=step.step_title,
        depends_on_previous=step.depends_on_previous,
        reference_field=step.reference_field,
        reason=step.reason,
    )


def _execute_single_intent(intent_result: IntentResult, context, message: str) -> dict:
    result = _dispatch_intent(intent_result, context, message)
    command = _build_command_preview(intent_result.intent, intent_result.filters, "执行主查询")
    return {
        "intent": intent_result.intent,
        "result": result,
        "commands": [command],
        "executed_steps": [
            {
                **command,
                "data_count": result["data_count"],
                "risk_flags": result["risk_flags"],
            }
        ],
    }


def _execute_plan(plan_result: QueryPlanResult, context, message: str) -> dict:
    commands = []
    executed_steps = []
    previous_result = {"rows": [], "data_count": 0, "risk_flags": []}
    final_intent = "unsupported"
    final_result = previous_result

    for raw_step in plan_result.steps[:3]:
        step = _inject_reference_filters(raw_step, previous_result)
        command = _build_command_preview(step.intent, step.filters, step.step_title)
        commands.append(command)

        permission_result = check_permission(context, step.intent, step.filters)
        if not permission_result["allowed"]:
            return {
                "error": {
                    "ok": False,
                    "error_code": permission_result["error_code"],
                    "message": permission_result["message"],
                }
            }

        intent_result = IntentResult(intent=step.intent, filters=step.filters, reason=step.reason)
        result = _dispatch_intent(intent_result, context, message)
        executed_steps.append(
            {
                **command,
                "data_count": result["data_count"],
                "risk_flags": result["risk_flags"],
            }
        )
        previous_result = result
        final_result = result
        final_intent = step.intent

        if step.intent == "unsupported":
            break

    return {
        "intent": final_intent,
        "result": final_result,
        "commands": commands,
        "executed_steps": executed_steps,
        "plan_reason": plan_result.reason,
    }


def handle_ai_chat(message: str, current_user) -> dict:
    text = (message or "").strip()
    if not text:
        return {
            "ok": False,
            "error_code": "EMPTY_MESSAGE",
            "message": "请输入要查询的问题。",
        }

    context = build_user_context(current_user)

    runtime = get_ai_runtime_status()
    if not runtime["available"]:
        return {
            "ok": False,
            "error_code": "AI_NOT_CONFIGURED",
            "message": runtime["message"],
            "ai_available": False,
        }

    plan_result = None
    if _should_attempt_nested_query(text):
        try:
            plan_result = _call_plan_model(text, context)
            plan_result = _repair_plan_result(plan_result, text, context)
        except Exception:
            plan_result = _rule_based_plan(text, context)

    if plan_result and plan_result.steps:
        first_step = plan_result.steps[0]
        primary_intent = IntentResult(intent=first_step.intent, filters=first_step.filters, reason=first_step.reason)
    else:
        try:
            primary_intent = _call_intent_model(text, context)
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "AI_INTENT_ERROR",
                "message": f"智能助手暂时无法解析当前问题：{sanitize_error_message(exc)}",
            }

        primary_intent.filters = normalize_filters(primary_intent.filters, text, derive_query_kind=False)
        primary_intent = _repair_intent_result(primary_intent, text, context)
        primary_intent.filters = normalize_filters(primary_intent.filters, text, derive_query_kind=False)

    primary_intent = _apply_analysis_override(text, context, primary_intent)
    primary_intent.filters = normalize_filters(primary_intent.filters, text, derive_query_kind=False)
    if primary_intent.filters.query_kind in ANALYSIS_QUERY_KINDS:
        plan_result = None

    if detect_permission_sensitive_request(text, context, primary_intent):
        return {
            "ok": False,
            "error_code": "PERMISSION_DENIED",
            "message": "当前角色无权查询该范围数据。",
        }

    if primary_intent.intent == "unsupported":
        return {
            "ok": True,
            "answer": "当前问题暂时不属于订单、投诉、工单、反馈、风险或统计查询范围。可以改成“查询我的待办工单”或“列出 P1 未关闭工单”这类问题继续查询。",
            "intent": "unsupported",
            "data_count": 0,
            "risk_flags": [],
            "rows": [],
            "commands": [],
            "executed_steps": [],
        }

    clarification = _build_query_clarification(text, primary_intent)
    if clarification:
        return clarification

    if primary_intent.intent == "ticket_detail" and not primary_intent.filters.ticket_id:
        return {
            "ok": True,
            "answer": "请补充要查看的工单编号，例如 T001。",
            "intent": "ticket_detail",
            "data_count": 0,
            "risk_flags": [],
            "rows": [],
            "commands": [],
            "executed_steps": [],
        }

    if primary_intent.intent == "action_suggestion" and not primary_intent.filters.ticket_id:
        return {
            "ok": True,
            "answer": "请补充工单编号，我可以基于当前状态给出只读处理建议。",
            "intent": "action_suggestion",
            "data_count": 0,
            "risk_flags": [],
            "rows": [],
            "commands": [],
            "executed_steps": [],
        }

    if plan_result and len(plan_result.steps) > 1:
        execution = _execute_plan(plan_result, context, text)
        if execution.get("error"):
            return execution["error"]
        intent = execution["intent"]
        result = execution["result"]
        commands = execution["commands"]
        executed_steps = execution["executed_steps"]
    else:
        permission_result = check_permission(context, primary_intent.intent, primary_intent.filters)
        if not permission_result["allowed"]:
            return {
                "ok": False,
                "error_code": permission_result["error_code"],
                "message": permission_result["message"],
            }
        execution = _execute_single_intent(primary_intent, context, text)
        intent = execution["intent"]
        result = execution["result"]
        commands = execution["commands"]
        executed_steps = execution["executed_steps"]

    verified_answer = _build_verified_answer(text, intent, result["rows"], result["risk_flags"])
    answer = verified_answer or _call_summary_model(
        text,
        intent,
        context,
        result["rows"],
        result["risk_flags"],
        result["data_count"],
        commands=commands,
    )

    return {
        "ok": True,
        "answer": answer,
        "intent": intent,
        "data_count": result["data_count"],
        "risk_flags": result["risk_flags"],
        "rows": result["rows"],
        "commands": commands,
        "executed_steps": executed_steps,
        **({"sql_debug": result.get("sql_debug", [])} if database.SQL_DEBUG_ENABLED else {}),
    }


def should_run_live_ai_tests() -> bool:
    return os.getenv("RUN_LIVE_AI_TESTS", "").strip().lower() == "true"


ACTION_KEYWORDS = (
    "创建投诉",
    "新增投诉",
    "发起投诉",
    "更新订单",
    "修改订单",
    "删除订单",
    "修改投诉",
    "删除投诉",
    "创建工单",
    "分派",
    "派给",
    "转给",
    "新增日志",
    "处理日志",
    "写入日志",
    "升级工单",
    "升级到",
    "修改优先级",
    "关闭工单",
    "待反馈",
    "提交反馈",
    "满意度",
    "重开工单",
    "删除日志",
    "撤销分派",
)


def get_role_example_actions(role: str) -> list[str]:
    return ROLE_EXAMPLE_ACTIONS.get(role, [])


# 查询意图信号词：出现这些词时，整句按“查询”处理，避免把“低满意度反馈/分派历史”这类名词误判为写操作
QUERY_SIGNAL_TOKENS = (
    "查询", "查一下", "查个", "查", "列出", "查看", "统计", "有哪些", "哪几", "哪些",
    "多少", "几条", "几次", "几个", "给我看", "帮我查", "最近", "本周", "本月",
    "今天", "近一周", "近期", "历史", "列表", "分布", "排名", "排行", "趋势",
    "概况", "概览", "汇总", "默认", "规则", "是否", "有没有", "平均", "占比", "比率",
    "未关闭", "未完结", "待处理",
)

# 真正的写操作动词（不带宾语也能判定为动作意图），名词如“满意度/反馈/分派/升级/待反馈”不在此列
WRITE_VERB_TOKENS = (
    "创建", "新增", "发起", "录入", "提交", "添加", "写入", "更新", "修改",
    "删除", "关闭", "重开", "分派", "派给", "转给", "转派", "升级", "撤销",
    "设为", "转为", "改为", "标记",
)


def detect_action_request(message: str) -> bool:
    """启发式 query/write 路由。仅作为离线/测试路由兜底（见 test_ai_command_matrix），
    线上判断已改用 classify_operation_type 由 LLM 动态决定。"""
    text = (message or "").strip()
    if not text:
        return False
    # 1. 明确的查询意图 → 一律按查询（优先级最高，杜绝名词误判）
    if any(token in text for token in QUERY_SIGNAL_TOKENS):
        return False
    # 2. 批量/高危/越权操作不是受控写动作，交给查询路径返回只读提示
    if any(token in text for token in ("删除所有", "删除全部", "清空", "批量", "所有工单", "全部工单", "所有投诉", "全部投诉")):
        return False
    # 3. “撤销分派” 是明确写操作
    if "撤销" in text and "分派" in text:
        return True
    if any(verb in text for verb in WRITE_VERB_TOKENS):
        return True
    return bool(
        re.search(
            r"(创建.*投诉|新增.*投诉|发起.*投诉|分派.*工单|派给.*工单|转派.*工单|升级.*工单|提交.*反馈|重开.*工单|关闭.*工单|删除.*订单|删除.*投诉|修改.*优先级|修改.*紧急程度|添加.*处理记录|新增.*处理记录|撤销.*分派)",
            text,
        )
    )


def _rule_based_action_safe(message: str, context) -> ActionIntentResult:
    del context
    text = (message or "").strip()
    payload = _normalize_action_payload(ActionIntentResult(action="unsupported").payload, text)
    lower = text.lower()

    if any(token in text for token in ("批量修改", "清空", "删除所有")):
        return ActionIntentResult(action="permission_sensitive", payload=payload, reason="识别到越权或高风险写操作")
    if "删除订单" in text:
        return ActionIntentResult(action="delete_order", payload=payload, reason="规则识别为删除订单")
    if "删除投诉" in text:
        return ActionIntentResult(action="delete_complaint", payload=payload, reason="规则识别为删除投诉")
    if ("更新订单" in text or "修改订单" in text) and payload.order_id:
        return ActionIntentResult(action="update_order_status", payload=payload, reason="规则识别为修改订单状态")
    if ("修改投诉" in text or "更新投诉" in text) and ("紧急程度" in text or payload.urgency_level):
        return ActionIntentResult(action="update_complaint_urgency", payload=payload, reason="规则识别为修改投诉紧急程度")
    if "创建工单" in text and payload.complaint_id:
        return ActionIntentResult(action="create_ticket_for_complaint", payload=payload, reason="规则识别为基于投诉创建工单")
    if "创建投诉" in text or "新增投诉" in text or ("投诉" in text and "ORD" in text.upper()):
        payload.complaint_content = payload.complaint_content or text
        return ActionIntentResult(action="create_complaint", payload=payload, reason="规则识别为创建投诉")
    if "撤销" in text and "分派" in text:
        return ActionIntentResult(action="revoke_assignment", payload=payload, reason="规则识别为撤销最新分派")
    if "分派" in text or "派给" in text or "转给" in text:
        name_match = re.search(r"(?:给|派给|分派给|转给)\s*([\u4e00-\u9fa5A-Za-z0-9]+)", text)
        if name_match and not payload.receiver_name:
            payload.receiver_name = name_match.group(1).strip("，。,. ")
        return ActionIntentResult(action="assign_ticket", payload=payload, reason="规则识别为分派工单")
    if "删除日志" in text or "删除处理日志" in text:
        log_match = re.search(r"\bLOG[A-Z0-9]+\b", text.upper())
        if log_match and not payload.log_id:
            payload.log_id = log_match.group(0)
        return ActionIntentResult(action="delete_action_log", payload=payload, reason="规则识别为删除处理日志")
    if ("删除工单" in text or ("删除" in text and "工单" in text)) and "日志" not in text:
        return ActionIntentResult(action="delete_ticket", payload=payload, reason="规则识别为删除工单")
    if "升级" in text:
        for option in ("主管复核", "跨部门协调", "平台管理层", "紧急专项组"):
            if option in text and not payload.to_level:
                payload.to_level = option
        return ActionIntentResult(action="escalate_ticket", payload=payload, reason="规则识别为升级工单")
    if "修改工单" in text and ("优先级" in text or payload.priority_level):
        return ActionIntentResult(action="update_ticket_priority", payload=payload, reason="规则识别为修改工单优先级")
    if "关闭工单" in text or ("状态" in text and "已关闭" in text):
        return ActionIntentResult(action="close_ticket", payload=payload, reason="规则识别为关闭工单")
    if "待反馈" in text:
        return ActionIntentResult(action="set_pending_feedback", payload=payload, reason="规则识别为设为待反馈")
    if "提交反馈" in text or "满意度" in text or "评分" in text:
        return ActionIntentResult(action="submit_feedback", payload=payload, reason="规则识别为提交反馈")
    if "重开工单" in text or ("重开" in text and "工单" in text):
        return ActionIntentResult(action="reopen_ticket", payload=payload, reason="规则识别为重开工单")
    if "日志" in text or "记录处理" in text or "写入日志" in text or "处理记录" in text:
        content_match = re.search(r"(?:内容是|内容为|记录为|写入)\s*(.+)$", text)
        if content_match and not payload.action_content:
            payload.action_content = content_match.group(1).strip()
        return ActionIntentResult(action="add_action_log", payload=payload, reason="规则识别为新增处理日志")
    if "create complaint" in lower or "assign" in lower or "feedback" in lower:
        return ActionIntentResult(action="permission_sensitive", payload=payload, reason="检测到英文写操作请求")
    return ActionIntentResult(action="unsupported", payload=payload, reason="未识别到受控写操作")


def _normalize_action_payload(payload, message: str):
    text = message or ""
    if not payload.ticket_id:
        match = re.search(r"\b(?:TCK[A-Z0-9]+|T\d{3})\b", text.upper())
        if match:
            payload.ticket_id = match.group(0)
    if not payload.complaint_id:
        match = re.search(r"\b(?:CMP[A-Z0-9]+|C\d{3})\b", text.upper())
        if match:
            payload.complaint_id = match.group(0)
    if not payload.order_id:
        match = re.search(r"\bORD\d{3}\b", text.upper())
        if match:
            payload.order_id = match.group(0)
    if not payload.employee_id:
        match = re.search(r"\b(?:EMP\d{3}|E\d{3})\b", text.upper())
        if match:
            payload.employee_id = match.group(0)
    if not payload.receiver_id and payload.employee_id and any(token in text for token in ("分派", "派给", "转给")):
        payload.receiver_id = payload.employee_id
    if "U1" in text and not payload.urgency_level:
        payload.urgency_level = "U1"
    elif "U2" in text and not payload.urgency_level:
        payload.urgency_level = "U2"
    elif "U3" in text and not payload.urgency_level:
        payload.urgency_level = "U3"
    elif "U4" in text and not payload.urgency_level:
        payload.urgency_level = "U4"
    if "紧急程度为高" in text or "紧急程度改为高" in text:
        payload.urgency_level = payload.urgency_level or "U1"
    if ("费用争议" in text or "多收费" in text or "退款" in text) and not payload.complaint_type:
        payload.complaint_type = "费用争议"
    elif ("服务态度" in text or "态度差" in text or "辱骂" in text or "拒载" in text) and not payload.complaint_type:
        payload.complaint_type = "司机服务"
    elif "司机服务" in text and not payload.complaint_type:
        payload.complaint_type = "司机服务"
    elif "安全事件" in text and not payload.complaint_type:
        payload.complaint_type = "安全事件"
    elif "取消争议" in text and not payload.complaint_type:
        payload.complaint_type = "取消争议"
    elif "物品遗失" in text and not payload.complaint_type:
        payload.complaint_type = "物品遗失"
    elif "平台异常" in text and not payload.complaint_type:
        payload.complaint_type = "平台异常"
    elif "其他问题" in text and not payload.complaint_type:
        payload.complaint_type = "其他问题"
    if not payload.priority_level:
        priority_match = re.search(r"\bP[1-4]\b", text.upper())
        if priority_match:
            payload.priority_level = priority_match.group(0)
    if not payload.order_status:
        for status in ("已完成", "已取消", "进行中", "异常"):
            if status in text:
                payload.order_status = status
                break
    level_matches = re.findall(r"\bL([1-4])\b", text.upper())
    if level_matches:
        if len(level_matches) >= 1 and not payload.from_level:
            payload.from_level = f"L{level_matches[0]}"
        if len(level_matches) >= 2 and not payload.to_level:
            payload.to_level = f"L{level_matches[1]}"
    if not payload.to_level:
        for option in ("主管复核", "跨部门协调", "平台管理层", "紧急专项组"):
            if option in text:
                payload.to_level = option
                break
    if payload.action_type is None:
        if "联系乘客" in text:
            payload.action_type = "联系乘客"
        elif "联系司机" in text:
            payload.action_type = "联系司机"
        elif "核查订单" in text:
            payload.action_type = "核查订单"
        elif "申请退款" in text:
            payload.action_type = "申请退款"
        elif "处罚司机" in text:
            payload.action_type = "处罚司机"
        elif "修改状态" in text:
            payload.action_type = "修改状态"
        elif "关闭工单" in text:
            payload.action_type = "关闭工单"
        elif "重开工单" in text:
            payload.action_type = "重开工单"
    if not payload.assignment_note:
        note_match = re.search(r"(?:备注是|备注为|备注)\s*(.+?)(?:。|$)", text)
        if note_match:
            payload.assignment_note = note_match.group(1).strip("，。 ")
    if not payload.complaint_content:
        complaint_match = re.search(r"(?:内容是|内容为)\s*(.+?)(?:。|$)", text)
        if complaint_match:
            payload.complaint_content = complaint_match.group(1).strip("，。 ")
    if not payload.action_content:
        action_match = re.search(r"(?:内容是|内容为|记录为|写入)\s*(.+?)(?:。|$)", text)
        if action_match:
            payload.action_content = action_match.group(1).strip("，。 ")
    if not payload.escalation_reason:
        escalation_match = re.search(r"(?:原因是|原因为)\s*(.+?)(?:。|$)", text)
        if escalation_match:
            payload.escalation_reason = escalation_match.group(1).strip("，。 ")
    if not payload.feedback_content:
        feedback_match = re.search(r"(?:反馈内容是|反馈内容为|内容是|内容为)\s*(.+?)(?:。|$)", text)
        if feedback_match:
            payload.feedback_content = feedback_match.group(1).strip("，。 ")
    if payload.satisfaction_score is None:
        score_match = re.search(r"([1-5])\s*分", text)
        if score_match:
            payload.satisfaction_score = int(score_match.group(1))
        else:
            score_match = re.search(r"([1-5])\s*星", text)
            if score_match:
                payload.satisfaction_score = int(score_match.group(1))
    if not payload.close_reason:
        close_match = re.search(r"(?:关闭原因是|关闭原因为|原因是|原因为)\s*(.+?)(?:。|$)", text)
        if close_match:
            payload.close_reason = close_match.group(1).strip("，。 ")
    return canonicalize_action_payload(payload)


def _rule_based_action(message: str, context) -> ActionIntentResult:
    text = (message or "").strip()
    payload = _normalize_action_payload(ActionIntentResult(action="unsupported").payload, text)
    lower = text.lower()

    if any(token in text for token in ("删除工单", "删除投诉", "批量修改", "清空", "删除所有")):
        return ActionIntentResult(action="permission_sensitive", payload=payload, reason="识别到越权或高风险写操作")
    if "创建投诉" in text or "新增投诉" in text or ("投诉" in text and "ORD" in text.upper()):
        payload.complaint_content = payload.complaint_content or text
        return ActionIntentResult(action="create_complaint", payload=payload, reason="规则识别为创建投诉")
    if "分派" in text or "派给" in text or "转给" in text:
        name_match = re.search(r"(?:给|派给|分派给|转给)\s*([\u4e00-\u9fa5A-Za-z0-9]+)", text)
        if name_match and not payload.receiver_name:
            payload.receiver_name = name_match.group(1).strip("，。,. ")
        return ActionIntentResult(action="assign_ticket", payload=payload, reason="规则识别为分派工单")
    if "删除日志" in text or "删除处理日志" in text:
        log_match = re.search(r"\bLOG[A-Z0-9]+\b", text.upper())
        if log_match and not payload.log_id:
            payload.log_id = log_match.group(0)
        return ActionIntentResult(action="delete_action_log", payload=payload, reason="规则识别为删除处理日志")
    if ("删除工单" in text or ("删除" in text and "工单" in text)) and "日志" not in text:
        return ActionIntentResult(action="delete_ticket", payload=payload, reason="规则识别为删除工单")
    if "升级" in text:
        for option in ("主管复核", "跨部门协调", "平台管理层", "紧急专项组"):
            if option in text and not payload.to_level:
                payload.to_level = option
        return ActionIntentResult(action="escalate_ticket", payload=payload, reason="规则识别为升级工单")
    if "待反馈" in text:
        return ActionIntentResult(action="set_pending_feedback", payload=payload, reason="规则识别为设为待反馈")
    if "提交反馈" in text or "满意度" in text or "评分" in text:
        return ActionIntentResult(action="submit_feedback", payload=payload, reason="规则识别为提交反馈")
    if "重开工单" in text or ("重开" in text and "工单" in text):
        return ActionIntentResult(action="reopen_ticket", payload=payload, reason="规则识别为重开工单")
    if "日志" in text or "记录处理" in text or "写入日志" in text:
        content_match = re.search(r"(?:内容是|内容为|记录为|写入)\s*(.+)$", text)
        if content_match and not payload.action_content:
            payload.action_content = content_match.group(1).strip()
        return ActionIntentResult(action="add_action_log", payload=payload, reason="规则识别为新增处理日志")
    if "create complaint" in lower or "assign" in lower or "feedback" in lower:
        return ActionIntentResult(action="permission_sensitive", payload=payload, reason="检测到英文写操作请求")
    return ActionIntentResult(action="unsupported", payload=payload, reason="未识别到受控写操作")


def _call_action_model(message: str, context) -> ActionIntentResult:
    llm = get_deepseek_llm(temperature=0.0)
    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        try:
            structured_llm = llm.with_structured_output(ActionIntentResult)
            result = structured_llm.invoke(
                [
                    SystemMessage(content=get_action_system_prompt(context)),
                    HumanMessage(content=message),
                ]
            )
            if isinstance(result, ActionIntentResult):
                return result
            return ActionIntentResult.model_validate(result)
        except Exception:
            pass

    result = call_deepseek_chat(
        [
            {
                "role": "system",
                "content": get_action_system_prompt(context)
                + "\n请只返回 JSON，字段必须包含 action、payload、reason。",
            },
            {"role": "user", "content": message},
        ],
        temperature=0.0,
    )
    if not result["ok"]:
        raise RuntimeError(result["message"])
    payload = _extract_json_payload(result["content"])
    return ActionIntentResult.model_validate(payload)


def _dispatch_action(action_result: ActionIntentResult, context) -> dict:
    action = action_result.action
    payload = action_result.payload
    if action == "create_complaint":
        return create_complaint_action(context, payload)
    if action == "update_order_status":
        return update_order_status_action(context, payload)
    if action == "delete_order":
        return delete_order_action(context, payload)
    if action == "update_complaint_urgency":
        return update_complaint_urgency_action(context, payload)
    if action == "delete_complaint":
        return delete_complaint_action(context, payload)
    if action == "delete_ticket":
        return delete_ticket_action(context, payload)
    if action == "create_ticket_for_complaint":
        return create_ticket_for_complaint_action(context, payload)
    if action == "assign_ticket":
        return assign_ticket_action(context, payload)
    if action == "add_action_log":
        return add_action_log_action(context, payload)
    if action == "escalate_ticket":
        return escalate_ticket_action(context, payload)
    if action == "update_ticket_priority":
        return update_ticket_priority_action(context, payload)
    if action == "close_ticket":
        return close_ticket_action(context, payload)
    if action == "set_pending_feedback":
        return set_pending_feedback_action(context, payload)
    if action == "submit_feedback":
        return submit_feedback_action(context, payload)
    if action == "reopen_ticket":
        return reopen_ticket_action(context, payload)
    if action == "revoke_assignment":
        return revoke_assignment_action(context, payload)
    if action == "delete_action_log":
        return delete_action_log_action(context, payload)
    return {"rows": [], "data_count": 0, "risk_flags": []}


def _build_action_command_preview(action_result: ActionIntentResult) -> dict:
    allowed_fields = {
        "create_complaint": {"order_id", "complaint_type", "urgency_level", "complaint_content"},
        "update_order_status": {"order_id", "order_status"},
        "delete_order": {"order_id"},
        "update_complaint_urgency": {"complaint_id", "urgency_level"},
        "delete_complaint": {"complaint_id"},
        "delete_ticket": {"ticket_id"},
        "create_ticket_for_complaint": {"complaint_id"},
        "assign_ticket": {"ticket_id", "receiver_id", "receiver_name", "assignment_note"},
        "add_action_log": {"ticket_id", "action_type", "action_content"},
        "escalate_ticket": {"ticket_id", "from_level", "to_level", "escalation_reason"},
        "update_ticket_priority": {"ticket_id", "priority_level"},
        "close_ticket": {"ticket_id", "close_reason"},
        "set_pending_feedback": {"ticket_id"},
        "submit_feedback": {"ticket_id", "satisfaction_score", "feedback_content"},
        "reopen_ticket": {"ticket_id"},
        "delete_action_log": {"log_id", "ticket_id", "delete_reason"},
        "revoke_assignment": {"ticket_id"},
    }.get(action_result.action, set())
    payload = {
        key: value
        for key, value in action_result.payload.model_dump().items()
        if value not in (None, "", [], {}) and (not allowed_fields or key in allowed_fields)
    }
    return {
        "step_title": f"执行 {action_result.action}",
        "tool": action_result.action,
        "intent": "write_action",
        "filters": payload,
    }


def handle_ai_action(message: str, current_user) -> dict:
    text = (message or "").strip()
    if not text:
        return {"ok": False, "error_code": "EMPTY_MESSAGE", "message": "请输入要执行的业务操作。"}

    context = build_user_context(current_user)
    runtime = get_ai_runtime_status()
    if not runtime["available"]:
        return {"ok": False, "error_code": "AI_NOT_CONFIGURED", "message": runtime["message"], "ai_available": False}

    try:
        action_result = _call_action_model(text, context)
    except Exception:
        action_result = _rule_based_action_safe(text, context)

    action_result.payload = _normalize_action_payload(action_result.payload, text)
    heuristic_action = _rule_based_action_safe(text, context)
    if action_result.action == "unsupported":
        action_result = heuristic_action
    elif action_result.action == "permission_sensitive" and heuristic_action.action not in {"unsupported", "permission_sensitive"}:
        action_result = heuristic_action

    permission_result = check_action_permission(context, action_result.action, action_result.payload)
    if not permission_result["allowed"]:
        return {
            "ok": False,
            "error_code": permission_result["error_code"],
            "message": permission_result["message"],
            "commands": [_build_action_command_preview(action_result)] if action_result.action != "unsupported" else [],
        }

    try:
        result = _dispatch_action(action_result, context)
    except PermissionError as exc:
        return {
            "ok": False,
            "error_code": "PERMISSION_DENIED",
            "message": str(exc),
            "commands": [_build_action_command_preview(action_result)],
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": "VALIDATION_ERROR",
            "message": str(exc),
            "commands": [_build_action_command_preview(action_result)],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "ACTION_EXECUTION_ERROR",
            "message": f"智能写操作执行失败：{sanitize_error_message(exc)}",
            "commands": [_build_action_command_preview(action_result)],
        }

    commands = [_build_action_command_preview(action_result)]
    answer = _call_summary_model(
        text,
        action_result.action,
        context,
        result["rows"],
        result["risk_flags"],
        result["data_count"],
        commands=commands,
    )
    return {
        "ok": True,
        "answer": answer,
        "intent": action_result.action,
        "data_count": result["data_count"],
        "risk_flags": result["risk_flags"],
        "rows": result["rows"],
        "commands": commands,
        "executed_steps": [
            {
                **commands[0],
                "data_count": result["data_count"],
                "risk_flags": result["risk_flags"],
            }
        ],
        **({"sql_debug": result.get("sql_debug", [])} if database.SQL_DEBUG_ENABLED else {}),
    }


def _attach_sql_debug(payload: dict, statements: list[dict]) -> dict:
    result = dict(payload or {})
    if not database.SQL_DEBUG_ENABLED:
        result.pop("sql_debug", None)
        result["debug_mode"] = False
        return result
    preview_debug = []
    if isinstance(result.get("rows"), list):
        pass
    if result.get("sql_debug") and isinstance(result.get("sql_debug"), list):
        preview_debug = list(result["sql_debug"])
    merged = list(statements or [])
    if preview_debug:
        merged.extend(preview_debug)
    result["sql_debug"] = merged
    result["debug_mode"] = True
    return result


def handle_ai_chat_with_debug(message: str, current_user) -> dict:
    tokens = begin_sql_debug_capture()
    try:
        result = handle_ai_chat(message, current_user)
    finally:
        sql_debug = end_sql_debug_capture(tokens)
    return _attach_sql_debug(result, sql_debug)


def handle_ai_action_with_debug(message: str, current_user) -> dict:
    tokens = begin_sql_debug_capture()
    try:
        result = handle_ai_action(message, current_user)
    finally:
        sql_debug = end_sql_debug_capture(tokens)
    return _attach_sql_debug(result, sql_debug)


def classify_operation_type(message: str, context) -> str:
    """由 LLM 动态判断本轮是查询还是受控写操作；AI 不可用或调用异常时安全降级为 query。"""
    text = (message or "").strip()
    if not text or not is_ai_available():
        return "query"
    prompt = get_operation_classifier_prompt(context)
    messages = [SystemMessage(content=prompt), HumanMessage(content=text)]
    # 先尝试结构化输出；部分供应商（如 DeepSeek 某些时段）不支持 response_format，失败则回退 chat
    llm = get_deepseek_llm(temperature=0.0)
    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        try:
            result = llm.with_structured_output(OperationTypeResult).invoke(messages)
            operation_type = getattr(result, "operation_type", None)
            if operation_type in {"query", "write"}:
                return operation_type
        except Exception:
            pass
    try:
        response = call_deepseek_chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
        if response.get("ok"):
            payload = _extract_json_payload(response["content"])
            operation_type = str(payload.get("operation_type", "")).strip().lower()
            if operation_type in {"query", "write"}:
                return operation_type
    except Exception:
        pass
    return "query"


def handle_ai_message_with_debug(message: str, current_user) -> dict:
    context = build_user_context(current_user)
    operation_type = classify_operation_type(message, context)
    handler = handle_ai_action_with_debug if operation_type == "write" else handle_ai_chat_with_debug
    result = handler(message, current_user)
    result["operation_type"] = operation_type
    result["operation_type_label"] = "写操作" if operation_type == "write" else "查询"
    return result
