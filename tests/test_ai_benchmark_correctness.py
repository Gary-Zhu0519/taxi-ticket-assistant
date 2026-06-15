from __future__ import annotations

from datetime import date, datetime, timedelta

import ai_service
from ai_schemas import IntentResult, QueryFilters
from models import Complaint, ComplaintType, Employee, Feedback, Ticket


def _enable_ai_runtime(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "get_ai_runtime_status",
        lambda: {"available": True, "model_name": "deepseek-v4-flash", "message": "benchmark"},
    )
    monkeypatch.setattr(ai_service, "_call_summary_model", lambda *args, **kwargs: "summary")
    monkeypatch.setattr(
        ai_service,
        "_call_analysis_intent_model",
        lambda message, context: IntentResult(intent="unsupported", filters=QueryFilters(), reason="mock analysis unsupported"),
    )


def _get_user(username: str) -> Employee:
    return Employee.query.filter_by(username=username).first()


def _recent_window():
    today = date.today()
    return today - timedelta(days=7), today + timedelta(days=1)


def _expected_department_overdue_count(department_name: str) -> int:
    return (
        Ticket.query.filter(
            Ticket.department.has(department_name=department_name),
            Ticket.ticket_status != "已关闭",
            Ticket.sla_deadline < datetime.now(),
        ).count()
    )


def _expected_open_fee_dispute_count() -> int:
    return (
        Ticket.query.join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .filter(
            ComplaintType.type_name == "费用争议",
            Ticket.ticket_status != "已关闭",
        )
        .count()
    )


def _expected_open_p1_count() -> int:
    return Ticket.query.filter(
        Ticket.priority_level == "P1",
        Ticket.ticket_status != "已关闭",
    ).count()


def _expected_recent_low_feedback_count() -> int:
    start_dt, end_dt = _recent_window()
    return (
        Feedback.query.filter(
            Feedback.satisfaction_score < 3,
            Feedback.feedback_time >= start_dt,
            Feedback.feedback_time < end_dt,
        ).count()
    )


def _expected_employee_todo_count(employee: Employee) -> int:
    return Ticket.query.filter(
        Ticket.current_owner_id == employee.employee_id,
        Ticket.ticket_status != "已关闭",
    ).count()


def _expected_department_pending_feedback_count(department_name: str) -> int:
    return Ticket.query.filter(
        Ticket.department.has(department_name=department_name),
        Ticket.ticket_status == "待反馈",
    ).count()


def _expected_open_count(department_name: str) -> int:
    return Ticket.query.filter(
        Ticket.department.has(department_name=department_name),
        Ticket.ticket_status != "已关闭",
    ).count()


def test_admin_department_overdue_count_repairs_alias_and_intent(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="ticket_query",
            filters=QueryFilters(department_name="财务部", is_overdue=True),
            reason="mock misclassified to ticket_query",
        ),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat("财务部有多少超时工单", user)
    expected = _expected_department_overdue_count("财务售后部")

    assert result["ok"] is True
    assert result["intent"] == "dashboard_summary"
    assert result["rows"][0]["department_name"] == "财务售后部"
    assert result["rows"][0]["overdue_tickets"] == expected
    assert str(expected) in result["answer"]


def test_admin_customer_service_overdue_count_from_unsupported(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="unsupported",
            filters=QueryFilters(),
            reason="mock unsupported",
        ),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat("客服有多少超时工单", user)
    expected = _expected_department_overdue_count("客服部")

    assert result["ok"] is True
    assert result["intent"] == "dashboard_summary"
    assert result["rows"][0]["department_name"] == "客服部"
    assert result["rows"][0]["overdue_tickets"] == expected
    assert str(expected) in result["answer"]


def test_admin_open_fee_dispute_count_matches_ground_truth(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="unsupported",
            filters=QueryFilters(),
            reason="mock unsupported",
        ),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat("查询费用争议类未关闭工单", user)
    expected = _expected_open_fee_dispute_count()

    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected
    assert all(row["complaint_type"] == "费用争议" for row in result["rows"])
    assert all(row["ticket_status"] != "已关闭" for row in result["rows"])


def test_admin_open_p1_count_matches_ground_truth(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="ticket_query",
            filters=QueryFilters(),
            reason="mock generic ticket query",
        ),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat("列出所有P1未关闭工单", user)
    expected = _expected_open_p1_count()

    assert result["ok"] is True
    assert result["intent"] == "risk_query"
    assert result["data_count"] == expected
    assert all(row["priority_level"] == "P1" for row in result["rows"])
    assert all(row["ticket_status"] != "已关闭" for row in result["rows"])


def test_admin_recent_low_feedback_count_matches_ground_truth(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="ticket_query",
            filters=QueryFilters(),
            reason="mock generic ticket query",
        ),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat("最近低满意度反馈有哪些", user)
    expected = _expected_recent_low_feedback_count()

    assert result["ok"] is True
    assert result["intent"] == "feedback_query"
    assert result["data_count"] == expected


def test_employee_todo_count_matches_ground_truth(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="unsupported",
            filters=QueryFilters(),
            reason="mock unsupported",
        ),
    )

    user = _get_user("employee")
    result = ai_service.handle_ai_chat("我的待处理工单有哪些", user)
    expected = _expected_employee_todo_count(user)

    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected
    assert all(row["current_owner"] == user.employee_name for row in result["rows"])


def test_finance_own_department_pending_feedback_count_allowed(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="dashboard_summary",
            filters=QueryFilters(department_name="财务售后部", ticket_status="待反馈"),
            reason="mock dashboard summary",
        ),
    )

    user = _get_user("finance")
    result = ai_service.handle_ai_chat("财务售后部待反馈工单有多少", user)
    expected = _expected_department_pending_feedback_count("财务售后部")

    assert result["ok"] is True
    assert result["intent"] == "dashboard_summary"
    assert result["rows"][0]["department_name"] == "财务售后部"
    assert result["rows"][0]["pending_feedback_tickets"] == expected
    assert str(expected) in result["answer"]


def test_employee_cross_user_query_is_denied(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(
            intent="ticket_query",
            filters=QueryFilters(employee_name="全部员工"),
            reason="mock cross-user query",
        ),
    )

    user = _get_user("employee")
    result = ai_service.handle_ai_chat("查询所有员工的待办工单", user)

    assert result["ok"] is False
    assert result["error_code"] == "PERMISSION_DENIED"


def test_detect_write_operation_request_does_not_misclassify_analysis_queries():
    assert ai_service.detect_write_operation_request(
        "找出所有反馈评分 <= 3 的工单，并分析是否发生过升级和平均处理日志数量"
    ) is False
    assert ai_service.detect_write_operation_request(
        "对比各部门工单处理数量、平均关闭时间、SLA 超时率和平均用户满意度，并排序输出综合绩效排名"
    ) is False


def test_admin_department_health_analysis(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(intent="unsupported", filters=QueryFilters(), reason="mock unsupported"),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat(
        "查询当前系统所有部门的工单健康状况，包括各部门未关闭工单数量、SLA 超时工单比例、平均处理时长、按优先级分布情况，并给出整体风险评级",
        user,
    )

    assert result["ok"] is True
    assert result["intent"] == "dashboard_summary"
    assert result["rows"]
    first = result["rows"][0]
    assert "open_ticket_count" in first
    assert "overdue_ratio" in first
    assert "avg_handle_hours" in first
    assert "p1_count" in first and "p2_count" in first and "p3_count" in first
    assert "risk_rating" in first
    customer_row = next(row for row in result["rows"] if row["department_name"] == "客服部")
    assert customer_row["open_ticket_count"] == _expected_open_count("客服部")


def test_admin_low_feedback_risk_analysis(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(intent="unsupported", filters=QueryFilters(), reason="mock unsupported"),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat(
        "找出所有反馈评分 <= 3 的工单，并分析对应投诉类型分布、是否发生过升级、平均处理日志数量、是否存在 SLA 超时，最终输出高风险未闭环工单列表",
        user,
    )

    assert result["ok"] is True
    assert result["intent"] == "feedback_query"
    assert result["rows"]
    assert result["rows"][0]["row_type"] == "analysis_summary"
    assert "high_risk_unclosed_count" in result["rows"][0]
    assert "avg_action_log_count" in result["rows"][0]


def test_admin_department_performance_ranking(monkeypatch, app_ctx):
    _enable_ai_runtime(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_call_intent_model",
        lambda message, context: IntentResult(intent="unsupported", filters=QueryFilters(), reason="mock unsupported"),
    )

    user = _get_user("admin")
    result = ai_service.handle_ai_chat(
        "对比各部门（客服/运营/安全/财务）：工单处理数量、平均关闭时间、SLA 超时率、平均用户满意度，并排序输出综合绩效排名",
        user,
    )

    assert result["ok"] is True
    assert result["intent"] == "dashboard_summary"
    assert result["rows"]
    assert "performance_rank" in result["rows"][0]
    assert "performance_score" in result["rows"][0]
    ranks = [row["performance_rank"] for row in result["rows"]]
    assert ranks == sorted(ranks)
