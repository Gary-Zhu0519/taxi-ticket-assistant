from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from flask import abort
from sqlalchemy import func, or_, text

from database import db
from models import (
    ActionLog,
    AssignmentRecord,
    Complaint,
    ComplaintType,
    Department,
    Employee,
    EscalationRecord,
    Feedback,
    Ticket,
)

ACTION_TYPES = [
    "联系乘客",
    "联系司机",
    "核查订单",
    "申请退款",
    "处罚司机",
    "修改状态",
    "关闭工单",
    "重开工单",
    "其他",
]

TO_LEVEL_OPTIONS = ["主管复核", "跨部门协同", "平台管理层", "紧急专项组"]

DEPARTMENT_NAME_BY_ROLE = {
    "customer_service": "客服部",
    "finance": "财务售后部",
    "safety": "安全部",
    "operation": "运营部",
}

ALLOWED_VIEW_NAMES = {
    "v_customer_service_ticket",
    "v_finance_complaint_ticket",
    "v_safety_ticket",
    "v_operation_ticket",
    "v_manager_ticket_summary",
    "v_employee_pending_ticket",
    "v_feedback_result",
}


def generate_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:10].upper()}"


def now() -> datetime:
    return datetime.now()


def is_ticket_overdue(ticket: Ticket) -> bool:
    return ticket.ticket_status != "已关闭" and ticket.sla_deadline < now()


def get_status_badge(status: str) -> str:
    return {
        "待分派": "secondary",
        "处理中": "primary",
        "已升级": "warning text-dark",
        "待反馈": "warning text-dark",
        "已关闭": "success",
        "已重开": "danger",
        "已受理": "secondary",
        "异常": "danger",
        "已完成": "success",
        "进行中": "primary",
        "已取消": "secondary",
    }.get(status, "secondary")


def get_priority_badge(priority: str) -> str:
    return {
        "P1": "danger",
        "P2": "warning text-dark",
        "P3": "primary",
        "P4": "secondary",
    }.get(priority, "secondary")


def get_urgency_badge(urgency: str) -> str:
    return {
        "U1": "danger",
        "U2": "warning text-dark",
        "U3": "primary",
        "U4": "secondary",
    }.get(urgency, "secondary")


def get_role_scope_name(role: str) -> str:
    return {
        "admin": "全量数据",
        "manager": "全量数据与统计",
        "customer_service": "客服部工单",
        "finance": "财务售后与费用争议工单",
        "safety": "安全事件与 P1 工单",
        "operation": "运营相关工单",
        "employee": "本人负责的未关闭工单",
    }.get(role, "默认范围")


def get_sidebar_menu_items(user: Employee | None):
    if user is None:
        return []

    menus = {
        "admin": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "订单管理", "endpoint": "main.orders", "match_prefix": "/orders"},
            {"label": "投诉管理", "endpoint": "main.complaints", "match_prefix": "/complaints"},
            {"label": "工单管理", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
            {"label": "统计看板", "endpoint": "main.dashboard", "match_prefix": "/dashboard"},
            {"label": "数据库设计说明", "endpoint": "main.schema", "match_prefix": "/schema"},
        ],
        "manager": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "工单管理", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
            {"label": "统计看板", "endpoint": "main.dashboard", "match_prefix": "/dashboard"},
            {"label": "投诉管理", "endpoint": "main.complaints", "match_prefix": "/complaints"},
            {"label": "数据库设计说明", "endpoint": "main.schema", "match_prefix": "/schema"},
        ],
        "customer_service": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "订单管理", "endpoint": "main.orders", "match_prefix": "/orders"},
            {"label": "投诉登记", "endpoint": "main.complaints", "match_prefix": "/complaints"},
            {"label": "工单管理", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
        ],
        "finance": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "财务售后工单", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
            {"label": "数据库设计说明", "endpoint": "main.schema", "match_prefix": "/schema"},
        ],
        "safety": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "安全工单", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
            {"label": "数据库设计说明", "endpoint": "main.schema", "match_prefix": "/schema"},
        ],
        "operation": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "运营工单", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
            {"label": "数据库设计说明", "endpoint": "main.schema", "match_prefix": "/schema"},
        ],
        "employee": [
            {"label": "首页", "endpoint": "main.index", "match_prefix": "/"},
            {"label": "我的待办工单", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "处理日志", "endpoint": "main.tickets", "match_prefix": "/tickets"},
            {"label": "智能增删改查助手", "endpoint": "ai.assistant_page", "match_prefix": "/assistant"},
        ],
    }
    return menus.get(user.role, [])


def fetch_view_rows(view_name: str, params: dict | None = None):
    if view_name not in ALLOWED_VIEW_NAMES:
        raise ValueError("Unsupported view name.")
    result = db.session.execute(text(f"SELECT * FROM {view_name}"), params or {})
    return [dict(row._mapping) for row in result]


def count_action_logs(ticket: Ticket) -> int:
    return ActionLog.query.filter_by(ticket_id=ticket.ticket_id).count()


def create_complaint_and_ticket(
    order,
    complaint_type: ComplaintType,
    complaint_content: str,
    urgency_level: str,
    complaint_time: datetime | None = None,
):
    complaint_time = complaint_time or now()
    complaint = Complaint(
        complaint_id=generate_id("CMP"),
        order_id=order.order_id,
        passenger_id=order.passenger_id,
        complaint_type_id=complaint_type.complaint_type_id,
        complaint_content=complaint_content.strip(),
        complaint_time=complaint_time,
        urgency_level=urgency_level,
        complaint_status="已受理",
    )
    db.session.add(complaint)
    db.session.flush()

    ticket = Ticket(
        ticket_id=generate_id("TCK"),
        complaint_id=complaint.complaint_id,
        priority_level=complaint_type.default_priority_level,
        ticket_status="待分派",
        department_id=complaint_type.default_department_id,
        current_owner_id=None,
        create_time=complaint_time,
        sla_deadline=complaint_time + timedelta(hours=complaint_type.default_sla_hours),
    )
    db.session.add(ticket)
    db.session.flush()
    return complaint, ticket


def assign_ticket(
    ticket: Ticket,
    assigner: Employee,
    receiver: Employee,
    note: str = "",
    assign_time: datetime | None = None,
):
    assign_time = assign_time or now()
    record = AssignmentRecord(
        assignment_id=generate_id("ASN"),
        ticket_id=ticket.ticket_id,
        assigner_id=assigner.employee_id,
        receiver_id=receiver.employee_id,
        department_id=receiver.department_id,
        assign_time=assign_time,
        assignment_note=note.strip() if note else None,
    )
    ticket.current_owner_id = receiver.employee_id
    ticket.department_id = receiver.department_id
    ticket.ticket_status = "处理中"
    ticket.complaint.complaint_status = "处理中"
    db.session.add(record)
    db.session.flush()
    return record


def add_action_log(
    ticket: Ticket,
    employee: Employee,
    action_type: str,
    action_content: str,
    action_time: datetime | None = None,
):
    if ticket.ticket_status == "已关闭":
        raise ValueError("已关闭工单不能新增处理日志，请先重开。")

    action_time = action_time or now()
    log = ActionLog(
        log_id=generate_id("LOG"),
        ticket_id=ticket.ticket_id,
        employee_id=employee.employee_id,
        action_type=action_type,
        action_content=action_content.strip(),
        action_time=action_time,
    )
    if ticket.ticket_status == "待分派" and ticket.current_owner_id:
        ticket.ticket_status = "处理中"
    if ticket.ticket_status in {"待分派", "已重开"}:
        ticket.ticket_status = "处理中"
    ticket.complaint.complaint_status = "处理中"
    db.session.add(log)
    db.session.flush()
    return log


def escalate_ticket(
    ticket: Ticket,
    employee: Employee,
    from_level: str,
    to_level: str,
    reason: str,
    escalation_time: datetime | None = None,
):
    escalation_time = escalation_time or now()
    record = EscalationRecord(
        escalation_id=generate_id("ESC"),
        ticket_id=ticket.ticket_id,
        from_level=from_level.strip(),
        to_level=to_level.strip(),
        escalation_reason=reason.strip(),
        escalated_by=employee.employee_id,
        escalation_time=escalation_time,
    )
    ticket.ticket_status = "已升级"
    ticket.complaint.complaint_status = "处理中"
    db.session.add(record)
    db.session.flush()
    return record


def set_ticket_pending_feedback(ticket: Ticket, employee: Employee):
    if ticket.ticket_status == "已关闭":
        raise ValueError("已关闭工单不能直接转为待反馈。")
    if ticket.feedback:
        raise ValueError("该工单已有反馈记录。")
    if count_action_logs(ticket) < 1:
        raise ValueError("工单关闭前至少需要一条处理日志。")

    ticket.ticket_status = "待反馈"
    ticket.complaint.complaint_status = "处理中"
    status_log = ActionLog(
        log_id=generate_id("LOG"),
        ticket_id=ticket.ticket_id,
        employee_id=employee.employee_id,
        action_type="修改状态",
        action_content="工单状态更新为待反馈。",
        action_time=now(),
    )
    db.session.add(status_log)
    db.session.flush()
    return status_log


def submit_feedback(
    ticket: Ticket,
    satisfaction_score: int,
    feedback_content: str,
    feedback_time: datetime | None = None,
):
    if ticket.feedback:
        raise ValueError("每个工单最多只能有一条反馈记录。")
    if not 1 <= satisfaction_score <= 5:
        raise ValueError("满意度评分必须在 1 到 5 之间。")
    if satisfaction_score >= 3 and count_action_logs(ticket) < 1:
        raise ValueError("工单关闭前至少需要一条处理日志。")

    feedback_time = feedback_time or now()
    feedback = Feedback(
        feedback_id=generate_id("FBK"),
        ticket_id=ticket.ticket_id,
        passenger_id=ticket.complaint.passenger_id,
        satisfaction_score=satisfaction_score,
        feedback_content=feedback_content.strip(),
        feedback_time=feedback_time,
    )
    db.session.add(feedback)

    if satisfaction_score >= 3:
        ticket.ticket_status = "已关闭"
        ticket.close_time = feedback_time
        ticket.complaint.complaint_status = "已关闭"
    else:
        ticket.ticket_status = "已重开"
        ticket.close_time = None
        ticket.complaint.complaint_status = "处理中"
    db.session.flush()
    return feedback


def reopen_ticket(ticket: Ticket, employee: Employee):
    ticket.ticket_status = "已重开"
    ticket.close_time = None
    ticket.complaint.complaint_status = "处理中"
    reopen_log = ActionLog(
        log_id=generate_id("LOG"),
        ticket_id=ticket.ticket_id,
        employee_id=employee.employee_id,
        action_type="重开工单",
        action_content="工单重新开启，继续补充处理。",
        action_time=now(),
    )
    db.session.add(reopen_log)
    db.session.flush()
    return reopen_log


def is_current_owner(user: Employee, ticket: Ticket) -> bool:
    return bool(user and ticket.current_owner_id == user.employee_id)


def can_create_complaint(user: Employee) -> bool:
    return user.role in {"admin", "manager", "customer_service"}


def can_assign_ticket(user: Employee, ticket: Ticket | None = None) -> bool:
    return user.role in {"admin", "manager", "customer_service"}


def can_escalate_ticket(user: Employee, ticket: Ticket) -> bool:
    return user.role in {"admin", "manager"} or is_current_owner(user, ticket)


def can_add_log(user: Employee, ticket: Ticket) -> bool:
    return user.role in {"admin", "manager"} or is_current_owner(user, ticket)


def can_set_pending_feedback(user: Employee, ticket: Ticket) -> bool:
    return user.role in {"admin", "manager"} or is_current_owner(user, ticket)


def can_submit_feedback(user: Employee, ticket: Ticket) -> bool:
    return user.role in {"admin", "manager", "customer_service"}


def can_reopen_ticket(user: Employee, ticket: Ticket) -> bool:
    return user.role in {"admin", "manager"} or is_current_owner(user, ticket)


def apply_ticket_scope(query, user: Employee):
    if user.role in {"admin", "manager"}:
        return query
    if user.role == "customer_service":
        return query.filter(
            or_(
                Department.department_name == "客服部",
                Ticket.ticket_status == "待分派",
            )
        )
    if user.role == "finance":
        return query.filter(
            or_(
                Department.department_name == "财务售后部",
                ComplaintType.type_name.in_(["费用争议", "支付退款", "多收费"]),
            )
        )
    if user.role == "safety":
        return query.filter(
            or_(
                Department.department_name == "安全部",
                ComplaintType.type_name == "安全事件",
                Ticket.priority_level == "P1",
            )
        )
    if user.role == "operation":
        return query.filter(
            or_(
                Department.department_name == "运营部",
                ComplaintType.type_name.in_(["司机服务", "取消争议", "车辆信息不符"]),
            )
        )
    if user.role == "employee":
        return query.filter(
            Ticket.current_owner_id == user.employee_id,
            Ticket.ticket_status != "已关闭",
        )
    return query.filter(Ticket.ticket_id == "__forbidden__")


def apply_complaint_scope(query, user: Employee):
    if user.role in {"admin", "manager"}:
        return query
    if user.role == "customer_service":
        return query.filter(
            or_(
                Department.department_name == "客服部",
                Ticket.ticket_status == "待分派",
            )
        )
    if user.role == "finance":
        return query.filter(
            or_(
                Department.department_name == "财务售后部",
                ComplaintType.type_name.in_(["费用争议", "支付退款", "多收费"]),
            )
        )
    if user.role == "safety":
        return query.filter(
            or_(
                Department.department_name == "安全部",
                ComplaintType.type_name == "安全事件",
                Ticket.priority_level == "P1",
            )
        )
    if user.role == "operation":
        return query.filter(
            or_(
                Department.department_name == "运营部",
                ComplaintType.type_name.in_(["司机服务", "取消争议", "车辆信息不符"]),
            )
        )
    if user.role == "employee":
        return query.filter(
            Ticket.current_owner_id == user.employee_id,
            Ticket.ticket_status != "已关闭",
        )
    return query.filter(Complaint.complaint_id == "__forbidden__")


def user_can_view_ticket(user: Employee, ticket_id: str) -> bool:
    query = (
        Ticket.query.join(Complaint)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .filter(Ticket.ticket_id == ticket_id)
    )
    return apply_ticket_scope(query, user).first() is not None


def require_ticket_access(user: Employee, ticket_id: str):
    if not user_can_view_ticket(user, ticket_id):
        abort(403)


def get_accessible_departments(user: Employee):
    if user.role in {"admin", "manager"}:
        return Department.query.order_by(Department.department_name).all()
    department_name = DEPARTMENT_NAME_BY_ROLE.get(user.role)
    if department_name:
        return Department.query.filter_by(department_name=department_name).all()
    return Department.query.filter_by(department_id=user.department_id).all()


def get_scope_stats(query):
    base_subquery = query.with_entities(Ticket.ticket_id).subquery()
    visible_ticket_ids = db.session.query(base_subquery.c.ticket_id)

    total_count = visible_ticket_ids.count()
    processing_count = Ticket.query.filter(
        Ticket.ticket_id.in_(visible_ticket_ids),
        Ticket.ticket_status.in_(["处理中", "已升级", "已重开"]),
    ).count()
    closed_count = Ticket.query.filter(
        Ticket.ticket_id.in_(visible_ticket_ids),
        Ticket.ticket_status == "已关闭",
    ).count()
    overdue_count = Ticket.query.filter(
        Ticket.ticket_id.in_(visible_ticket_ids),
        Ticket.ticket_status != "已关闭",
        Ticket.sla_deadline < now(),
    ).count()
    average_score = db.session.query(func.avg(Feedback.satisfaction_score)).filter(
        Feedback.ticket_id.in_(visible_ticket_ids)
    ).scalar()
    return {
        "total_count": total_count,
        "processing_count": processing_count,
        "closed_count": closed_count,
        "overdue_count": overdue_count,
        "average_score": round(float(average_score), 2) if average_score else None,
    }
