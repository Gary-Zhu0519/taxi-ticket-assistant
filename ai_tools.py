from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import joinedload, selectinload

from database import db
from ai_permissions import apply_ticket_scope, get_allowed_dashboard_departments, mask_sensitive_fields
from ai_schemas import ActionPayload, QueryFilters
from models import (
    ActionLog,
    AssignmentRecord,
    Complaint,
    ComplaintType,
    Department,
    Driver,
    Employee,
    EscalationRecord,
    Feedback,
    Passenger,
    RideOrder,
    Ticket,
)
from services import (
    ACTION_TYPES,
    TO_LEVEL_OPTIONS,
    add_action_log,
    assign_ticket,
    can_add_log,
    can_assign_ticket,
    can_create_complaint,
    can_escalate_ticket,
    can_reopen_ticket,
    can_set_pending_feedback,
    can_submit_feedback,
    count_action_logs,
    create_complaint_and_ticket,
    escalate_ticket,
    generate_id,
    now,
    reopen_ticket,
    set_ticket_pending_feedback,
    submit_feedback,
)

PROCESSING_STATUSES = {"待分派", "处理中", "已升级", "待反馈", "已重开"}


def _nth_query_identifier(query, attr_name: str, index: int):
    if index < 1:
        return None
    row = query.offset(index - 1).limit(1).first()
    return getattr(row, attr_name) if row else None


def resolve_passenger_identifier(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    upper = raw_id.strip().upper()
    if re.fullmatch(r"PSG\d{3}", upper):
        return upper
    match = re.fullmatch(r"P(\d{3})", upper)
    if match:
        return f"PSG{match.group(1)}"
    return upper


def resolve_driver_identifier(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    upper = raw_id.strip().upper()
    if re.fullmatch(r"DRV\d{3}", upper):
        return upper
    match = re.fullmatch(r"D(\d{3})", upper)
    if match:
        return f"DRV{match.group(1)}"
    return upper


def resolve_employee_identifier(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    upper = raw_id.strip().upper()
    if re.fullmatch(r"EMP\d{3}", upper):
        return upper
    match = re.fullmatch(r"E(\d{3})", upper)
    if match:
        return f"EMP{match.group(1)}"
    return upper


def resolve_complaint_identifier(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    upper = raw_id.strip().upper()
    if upper.startswith("CMP"):
        return upper
    match = re.fullmatch(r"C(\d{3})", upper)
    if match:
        resolved = _nth_query_identifier(
            Complaint.query.order_by(Complaint.complaint_time.asc(), Complaint.complaint_id.asc()),
            "complaint_id",
            int(match.group(1)),
        )
        return resolved or upper
    return upper


def resolve_ticket_identifier(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    upper = raw_id.strip().upper()
    if upper.startswith("TCK"):
        return upper
    match = re.fullmatch(r"T(\d{3})", upper)
    if match:
        resolved = _nth_query_identifier(
            Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()),
            "ticket_id",
            int(match.group(1)),
        )
        return resolved or upper
    return upper


def _expand_ticket_ids(raw) -> list[str]:
    """支持多工单号输入（如 'T001 和 T002'、"['T001','T002']"、'T001,T002'），返回解析后的工单 id 列表。"""
    text = str(raw or "").upper()
    tokens = re.findall(r"TCK[A-Z0-9]+|T\d{3}", text)
    if not tokens:
        return [text] if text else []
    resolved: list[str] = []
    for token in tokens:
        rid = resolve_ticket_identifier(token)
        if rid and rid not in resolved:
            resolved.append(rid)
    return resolved or ([text] if text else [])


def canonicalize_query_filters(filters: QueryFilters) -> QueryFilters:
    normalized = filters.model_copy(deep=True)
    normalized.ticket_id = resolve_ticket_identifier(normalized.ticket_id)
    normalized.complaint_id = resolve_complaint_identifier(normalized.complaint_id)
    normalized.passenger_id = resolve_passenger_identifier(normalized.passenger_id)
    normalized.driver_id = resolve_driver_identifier(normalized.driver_id)
    normalized.employee_id = resolve_employee_identifier(normalized.employee_id)
    if normalized.employee_id and not normalized.employee_name:
        employee = Employee.query.filter_by(employee_id=normalized.employee_id).first()
        if employee:
            normalized.employee_name = employee.employee_name
    return normalized


def canonicalize_action_payload(payload: ActionPayload) -> ActionPayload:
    normalized = payload.model_copy(deep=True)
    normalized.ticket_id = resolve_ticket_identifier(normalized.ticket_id)
    normalized.complaint_id = resolve_complaint_identifier(normalized.complaint_id)
    normalized.employee_id = resolve_employee_identifier(normalized.employee_id)
    normalized.receiver_id = resolve_employee_identifier(normalized.receiver_id)
    return normalized


def _limit_value(filters: QueryFilters) -> int:
    return max(1, min(filters.limit or 10, 50))


def _parse_date_bounds(filters: QueryFilters):
    start_dt = None
    end_dt = None

    if filters.date_start:
        try:
            start_dt = datetime.fromisoformat(filters.date_start)
        except ValueError:
            try:
                start_dt = datetime.strptime(filters.date_start, "%Y-%m-%d")
            except ValueError:
                start_dt = None

    if filters.date_end:
        try:
            end_dt = datetime.fromisoformat(filters.date_end)
        except ValueError:
            try:
                end_dt = datetime.strptime(filters.date_end, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                end_dt = None

    return start_dt, end_dt


def _compile_query_sql(query, label: str = "query") -> dict:
    try:
        compiled = query.statement.compile(
            dialect=db.engine.dialect,
            compile_kwargs={"literal_binds": True},
        )
        return {
            "statement": str(compiled).strip(),
            "parameters": None,
            "executemany": False,
            "source": "orm_preview",
            "label": label,
        }
    except Exception:
        try:
            compiled = query.statement.compile(dialect=db.engine.dialect)
            return {
                "statement": str(compiled).strip(),
                "parameters": getattr(compiled, "params", None),
                "executemany": False,
                "source": "orm_preview",
                "label": label,
            }
        except Exception:
            return {
                "statement": f"-- unable to compile SQL preview for {label}",
                "parameters": None,
                "executemany": False,
                "source": "orm_preview",
                "label": label,
            }


def _ticket_base_query():
    return (
        Ticket.query.join(Complaint)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .join(RideOrder, Complaint.order_id == RideOrder.order_id)
        .join(Passenger, RideOrder.passenger_id == Passenger.passenger_id)
        .join(Driver, RideOrder.driver_id == Driver.driver_id)
        .options(
            joinedload(Ticket.complaint).joinedload(Complaint.complaint_type).joinedload(ComplaintType.default_department),
            joinedload(Ticket.complaint).joinedload(Complaint.order).joinedload(RideOrder.passenger),
            joinedload(Ticket.complaint).joinedload(Complaint.order).joinedload(RideOrder.driver),
            joinedload(Ticket.department).joinedload(Department.manager),
            joinedload(Ticket.current_owner),
            joinedload(Ticket.feedback),
            joinedload(Ticket.action_logs).joinedload(ActionLog.employee),
            selectinload(Ticket.assignments).joinedload(AssignmentRecord.receiver),
        )
    )


def _apply_ticket_filters(query, filters: QueryFilters):
    if filters.ticket_id:
        ticket_ids = _expand_ticket_ids(filters.ticket_id)
        if len(ticket_ids) > 1:
            query = query.filter(Ticket.ticket_id.in_(ticket_ids))
        elif ticket_ids:
            query = query.filter(Ticket.ticket_id == ticket_ids[0])
    if filters.complaint_id:
        query = query.filter(Ticket.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(RideOrder.order_id == filters.order_id)
    if filters.driver_id:
        query = query.filter(RideOrder.driver_id == filters.driver_id)
    if filters.ticket_status:
        if filters.ticket_status == "未关闭":
            query = query.filter(Ticket.ticket_status != "已关闭")
        else:
            query = query.filter(Ticket.ticket_status == filters.ticket_status)
    if filters.priority_level:
        query = query.filter(Ticket.priority_level == filters.priority_level)
    if filters.complaint_type:
        query = query.filter(ComplaintType.type_name == filters.complaint_type)
    if filters.department_name:
        query = query.filter(Department.department_name == filters.department_name)
    if filters.employee_id and filters.query_kind not in PARTICIPATION_QUERY_KINDS:
        query = query.filter(Ticket.current_owner_id == filters.employee_id)
    if filters.employee_name and filters.query_kind not in PARTICIPATION_QUERY_KINDS:
        query = query.join(Employee, Ticket.current_owner_id == Employee.employee_id).filter(
            Employee.employee_name == filters.employee_name
        )
    if filters.urgency_level:
        query = query.filter(Complaint.urgency_level == filters.urgency_level)
    if filters.query_kind == "near_sla":
        soon_deadline = datetime.now() + timedelta(hours=24)
        query = query.filter(
            Ticket.ticket_status != "已关闭",
            Ticket.sla_deadline >= datetime.now(),
            Ticket.sla_deadline <= soon_deadline,
        )
    if filters.is_overdue is True:
        query = query.filter(Ticket.ticket_status != "已关闭", Ticket.sla_deadline < datetime.now())
    elif filters.is_overdue is False:
        query = query.filter(
            (Ticket.ticket_status == "已关闭") | (Ticket.sla_deadline >= datetime.now())
        )

    if filters.sla_within_hours is not None:
        soon_deadline = datetime.now() + timedelta(hours=filters.sla_within_hours)
        query = query.filter(
            Ticket.ticket_status != "已关闭",
            Ticket.sla_deadline >= datetime.now(),
            Ticket.sla_deadline <= soon_deadline,
        )
    if filters.has_assignment is True:
        query = query.filter(Ticket.assignments.any())
    elif filters.has_assignment is False:
        query = query.filter(~Ticket.assignments.any())
    if filters.has_action_log is True:
        query = query.filter(Ticket.action_logs.any())
    elif filters.has_action_log is False:
        query = query.filter(~Ticket.action_logs.any())
    if filters.has_escalation is True:
        query = query.filter(Ticket.escalations.any())
    elif filters.has_escalation is False:
        query = query.filter(~Ticket.escalations.any())
    feedback_exists = select(Feedback.feedback_id).where(Feedback.ticket_id == Ticket.ticket_id).exists()
    if filters.has_feedback is True:
        query = query.filter(feedback_exists)
    elif filters.has_feedback is False:
        query = query.filter(~feedback_exists)

    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(Ticket.create_time >= start_dt)
    if end_dt:
        query = query.filter(Ticket.create_time < end_dt)

    return query


def _ticket_to_row(ticket: Ticket) -> dict:
    complaint = ticket.complaint
    complaint_type = complaint.complaint_type if complaint else None
    order = complaint.order if complaint else None
    passenger = order.passenger if order else None
    driver = order.driver if order else None
    return {
        "ticket_id": ticket.ticket_id,
        "order_id": order.order_id if order else "-",
        "complaint_id": complaint.complaint_id if complaint else "-",
        "complaint_type": complaint_type.type_name if complaint_type else "-",
        "complaint_content": complaint.complaint_content if complaint else "-",
        "default_department_name": complaint_type.default_department.department_name if complaint_type and complaint_type.default_department else "-",
        "default_sla_hours": complaint_type.default_sla_hours if complaint_type else "-",
        "urgency_level": complaint.urgency_level if complaint else "-",
        "priority_level": ticket.priority_level,
        "ticket_status": ticket.ticket_status,
        "department_name": ticket.department.department_name if ticket.department else "-",
        "department_manager": ticket.department.manager.employee_name if ticket.department and ticket.department.manager else "-",
        "current_owner": ticket.current_owner.employee_name if ticket.current_owner else "未分派",
        "latest_assignee": ticket.assignments[0].receiver.employee_name if ticket.assignments and ticket.assignments[0].receiver else "-",
        "is_owner_consistent": bool(ticket.assignments) and ticket.current_owner_id is not None and ticket.assignments[0].receiver_id == ticket.current_owner_id,
        "passenger_name": passenger.passenger_name if passenger else "-",
        "passenger_phone": passenger.phone if passenger else "-",
        "driver_name": driver.driver_name if driver else "-",
        "driver_phone": driver.phone if driver else "-",
        "create_time": ticket.create_time.strftime("%Y-%m-%d %H:%M:%S"),
        "sla_deadline": ticket.sla_deadline.strftime("%Y-%m-%d %H:%M:%S"),
        "close_time": ticket.close_time.strftime("%Y-%m-%d %H:%M:%S") if ticket.close_time else "-",
        "latest_action_content": ticket.action_logs[0].action_content if ticket.action_logs else "-",
        "is_overdue": ticket.ticket_status != "已关闭" and ticket.sla_deadline < datetime.now(),
    }


def _derive_risk_flags(rows: list[dict]) -> list[str]:
    flags = set()
    for row in rows:
        if row.get("priority_level") == "P1":
            flags.add("存在P1工单")
        if row.get("is_overdue") is True:
            flags.add("存在超时工单")
        if row.get("ticket_status") == "已升级":
            flags.add("存在已升级工单")
        if row.get("ticket_status") == "待反馈":
            flags.add("存在待反馈工单")
        if row.get("complaint_type") == "安全事件":
            flags.add("存在安全事件")
        score = row.get("satisfaction_score")
        if isinstance(score, int) and score < 3:
            flags.add("存在低满意度反馈")
    return sorted(flags)


def _empty_result():
    return {"rows": [], "data_count": 0, "risk_flags": [], "sql_debug": []}


PARTICIPATION_QUERY_KINDS = {
    "employee_participation",
    "employee_handled_tickets_with_current_assignee",
    "employee_handled",
}


def _employee_participation_roles(employee_id: str) -> dict[str, set[str]]:
    """返回 {ticket_id: {角色}}，覆盖员工作为 当前负责人/分派人/接收人/日志记录人/升级发起人 参与过的全部工单。"""
    roles: dict[str, set[str]] = {}
    for (ticket_id,) in db.session.query(Ticket.ticket_id).filter(Ticket.current_owner_id == employee_id).all():
        roles.setdefault(ticket_id, set()).add("当前负责人")
    for (ticket_id,) in (
        AssignmentRecord.query.filter(
            (AssignmentRecord.assigner_id == employee_id) | (AssignmentRecord.receiver_id == employee_id)
        )
        .with_entities(AssignmentRecord.ticket_id)
        .distinct()
    ):
        roles.setdefault(ticket_id, set()).add("分派参与")
    for (ticket_id,) in (
        ActionLog.query.filter(ActionLog.employee_id == employee_id)
        .with_entities(ActionLog.ticket_id)
        .distinct()
    ):
        roles.setdefault(ticket_id, set()).add("处理日志记录人")
    for (ticket_id,) in (
        EscalationRecord.query.filter(EscalationRecord.escalated_by == employee_id)
        .with_entities(EscalationRecord.ticket_id)
        .distinct()
    ):
        roles.setdefault(ticket_id, set()).add("升级发起人")
    return roles


def _ticket_timeline_row(event_type: str, event_time: datetime | None, actor_name: str, detail: str, ticket: Ticket) -> dict:
    return {
        "ticket_id": ticket.ticket_id,
        "event_type": event_type,
        "event_time": event_time.strftime("%Y-%m-%d %H:%M:%S") if event_time else "-",
        "actor_name": actor_name or "-",
        "detail": detail or "-",
        "ticket_status": ticket.ticket_status,
        "department_name": ticket.department.department_name if ticket.department else "-",
    }


def _build_ticket_timeline(ticket: Ticket) -> list[dict]:
    rows = [
        _ticket_timeline_row("工单创建", ticket.create_time, ticket.current_owner.employee_name if ticket.current_owner else "系统", "投诉创建后自动生成工单", ticket)
    ]
    for assignment in sorted(ticket.assignments, key=lambda item: item.assign_time):
        rows.append(
            _ticket_timeline_row(
                "工单分派",
                assignment.assign_time,
                assignment.assigner.employee_name if assignment.assigner else "-",
                f"分派给 {assignment.receiver.employee_name if assignment.receiver else '-'} / {assignment.department.department_name if assignment.department else '-'}",
                ticket,
            )
        )
    for escalation in sorted(ticket.escalations, key=lambda item: item.escalation_time):
        rows.append(
            _ticket_timeline_row(
                "工单升级",
                escalation.escalation_time,
                escalation.escalated_by_employee.employee_name if escalation.escalated_by_employee else "-",
                f"{escalation.from_level} -> {escalation.to_level}：{escalation.escalation_reason}",
                ticket,
            )
        )
    for log in sorted(ticket.action_logs, key=lambda item: item.action_time):
        rows.append(
            _ticket_timeline_row(
                f"处理日志/{log.action_type}",
                log.action_time,
                log.employee.employee_name if log.employee else "-",
                log.action_content,
                ticket,
            )
        )
    if ticket.feedback:
        rows.append(
            _ticket_timeline_row(
                "乘客反馈",
                ticket.feedback.feedback_time,
                ticket.feedback.passenger.passenger_name if ticket.feedback.passenger else "-",
                f"满意度 {ticket.feedback.satisfaction_score} 分：{ticket.feedback.feedback_content}",
                ticket,
            )
        )
    return rows


def query_ticket_lifecycle(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    ticket_ids = _expand_ticket_ids(filters.ticket_id)
    if not ticket_ids:
        return _empty_result()

    detail_query = apply_ticket_scope(_ticket_base_query(), context).filter(Ticket.ticket_id.in_(ticket_ids))
    sql_debug = [_compile_query_sql(detail_query, "query_ticket_lifecycle")]
    tickets = detail_query.all()
    if not tickets:
        return _empty_result()

    rows: list[dict] = []
    for ticket in tickets:
        rows.extend(_build_ticket_timeline(ticket))

    masked_rows = [mask_sensitive_fields(row, context) for row in rows]
    return {
        "rows": masked_rows,
        "data_count": len(masked_rows),
        "risk_flags": _derive_risk_flags([_ticket_to_row(ticket) for ticket in tickets]),
        "sql_debug": sql_debug,
    }


def query_assignments(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    query = (
        AssignmentRecord.query.join(Ticket, AssignmentRecord.ticket_id == Ticket.ticket_id)
        .join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .options(
            joinedload(AssignmentRecord.assigner),
            joinedload(AssignmentRecord.receiver),
            joinedload(AssignmentRecord.department),
        )
    )
    query = apply_ticket_scope(query, context)
    if filters.ticket_id:
        ticket_ids = _expand_ticket_ids(filters.ticket_id)
        if len(ticket_ids) > 1:
            query = query.filter(AssignmentRecord.ticket_id.in_(ticket_ids))
        elif ticket_ids:
            query = query.filter(AssignmentRecord.ticket_id == ticket_ids[0])
    if filters.employee_id:
        query = query.filter(
            (AssignmentRecord.assigner_id == filters.employee_id) | (AssignmentRecord.receiver_id == filters.employee_id)
        )
    if filters.employee_name:
        query = query.join(Employee, AssignmentRecord.receiver_id == Employee.employee_id).filter(
            Employee.employee_name == filters.employee_name
        )
    if filters.complaint_id:
        query = query.filter(Complaint.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(Complaint.order_id == filters.order_id)
    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(AssignmentRecord.assign_time >= start_dt)
    if end_dt:
        query = query.filter(AssignmentRecord.assign_time < end_dt)
    total_count = query.order_by(None).count()
    query = query.order_by(AssignmentRecord.assign_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_assignments")]
    rows = []
    for record in query.all():
        row = {
            "assignment_id": record.assignment_id,
            "ticket_id": record.ticket_id,
            "assigner_name": record.assigner.employee_name if record.assigner else "-",
            "receiver_name": record.receiver.employee_name if record.receiver else "-",
            "department_name": record.department.department_name if record.department else "-",
            "assign_time": record.assign_time.strftime("%Y-%m-%d %H:%M:%S"),
            "assignment_note": record.assignment_note or "-",
        }
        rows.append(mask_sensitive_fields(row, context))
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_escalations(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    query = (
        EscalationRecord.query.join(Ticket, EscalationRecord.ticket_id == Ticket.ticket_id)
        .join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .options(joinedload(EscalationRecord.escalated_by_employee))
    )
    query = apply_ticket_scope(query, context)
    if filters.ticket_id:
        query = query.filter(EscalationRecord.ticket_id == filters.ticket_id)
    if filters.employee_id:
        query = query.filter(EscalationRecord.escalated_by == filters.employee_id)
    if filters.to_level:
        query = query.filter(EscalationRecord.to_level == filters.to_level)
    if filters.complaint_id:
        query = query.filter(Complaint.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(Complaint.order_id == filters.order_id)
    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(EscalationRecord.escalation_time >= start_dt)
    if end_dt:
        query = query.filter(EscalationRecord.escalation_time < end_dt)
    total_count = query.order_by(None).count()
    query = query.order_by(EscalationRecord.escalation_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_escalations")]
    rows = []
    for record in query.all():
        ticket = record.ticket
        row = {
            "escalation_id": record.escalation_id,
            "ticket_id": record.ticket_id,
            "from_level": record.from_level,
            "to_level": record.to_level,
            "escalation_reason": record.escalation_reason,
            "escalated_by": record.escalated_by_employee.employee_name if record.escalated_by_employee else "-",
            "escalation_time": record.escalation_time.strftime("%Y-%m-%d %H:%M:%S"),
            "ticket_status": ticket.ticket_status if ticket else "-",
        }
        rows.append(mask_sensitive_fields(row, context))
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_action_logs(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    query = (
        ActionLog.query.join(Ticket, ActionLog.ticket_id == Ticket.ticket_id)
        .join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .options(joinedload(ActionLog.employee))
    )
    query = apply_ticket_scope(query, context)
    if filters.ticket_id:
        query = query.filter(ActionLog.ticket_id == filters.ticket_id)
    if filters.employee_id:
        query = query.filter(ActionLog.employee_id == filters.employee_id)
    elif filters.employee_name:
        query = query.join(Employee, ActionLog.employee_id == Employee.employee_id).filter(
            Employee.employee_name == filters.employee_name
        )
    if filters.complaint_id:
        query = query.filter(Complaint.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(Complaint.order_id == filters.order_id)
    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(ActionLog.action_time >= start_dt)
    if end_dt:
        query = query.filter(ActionLog.action_time < end_dt)
    total_count = query.order_by(None).count()
    query = query.order_by(ActionLog.action_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_action_logs")]
    rows = []
    for log in query.all():
        row = {
            "log_id": log.log_id,
            "ticket_id": log.ticket_id,
            "employee_id": log.employee_id,
            "employee_name": log.employee.employee_name if log.employee else "-",
            "action_type": log.action_type,
            "action_content": log.action_content,
            "action_time": log.action_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows.append(mask_sensitive_fields(row, context))
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_complaint_type_rules(context, filters: QueryFilters):
    del context
    query = ComplaintType.query.join(Department, ComplaintType.default_department_id == Department.department_id)
    if filters.complaint_type:
        query = query.filter(ComplaintType.type_name == filters.complaint_type)
    query = query.order_by(ComplaintType.type_name.asc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_complaint_type_rules")]
    rows = [
        {
            "complaint_type": item.type_name,
            "type_description": item.type_description,
            "default_department_name": item.default_department.department_name if item.default_department else "-",
            "default_priority_level": item.default_priority_level,
            "default_sla_hours": item.default_sla_hours,
        }
        for item in query.all()
    ]
    return {"rows": rows, "data_count": len(rows), "risk_flags": [], "sql_debug": sql_debug}


def query_driver_order_stats(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    base = query_orders(context, QueryFilters(driver_id=filters.driver_id, limit=50))
    if not filters.driver_id:
        return _empty_result()
    driver = Driver.query.filter_by(driver_id=filters.driver_id).first()
    if not driver:
        return _empty_result()
    visible_orders = base["rows"]
    total_orders = len(visible_orders)
    completed_orders = sum(1 for row in visible_orders if row["order_status"] == "已完成")
    completion_rate = round((completed_orders / total_orders) * 100, 2) if total_orders else 0.0
    row = {
        "driver_id": driver.driver_id,
        "driver_name": driver.driver_name,
        "total_orders": total_orders,
        "completed_orders": completed_orders,
        "completion_rate": completion_rate,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1 if total_orders or driver else 0, "risk_flags": [], "sql_debug": base.get("sql_debug", [])}


def query_feedback_stats(context, filters: QueryFilters):
    query = (
        db.session.query(
            ComplaintType.type_name.label("complaint_type"),
            func.count(Feedback.feedback_id).label("feedback_count"),
            func.avg(Feedback.satisfaction_score).label("avg_satisfaction_score"),
        )
        .join(Complaint, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
        .join(Feedback, Feedback.ticket_id == Ticket.ticket_id)
    )
    query = apply_ticket_scope(query, context)
    query = query.group_by(ComplaintType.type_name).order_by(
        func.count(Feedback.feedback_id).desc(),
        ComplaintType.type_name.asc(),
    ).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_feedback_stats")]
    rows = [
        {
            "complaint_type": item.complaint_type,
            "feedback_count": int(item.feedback_count or 0),
            "avg_satisfaction_score": round(float(item.avg_satisfaction_score or 0), 2),
        }
        for item in query.all()
    ]
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_tickets(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    if filters.query_kind == "ticket_lifecycle":
        return query_ticket_lifecycle(context, filters)
    if filters.query_kind == "assignment_history":
        return query_assignments(context, filters)
    if filters.query_kind == "escalation_history":
        return query_escalations(context, filters)
    if filters.query_kind == "action_log_history":
        return query_action_logs(context, filters)

    participation = None
    if filters.query_kind in PARTICIPATION_QUERY_KINDS:
        emp_id = filters.employee_id
        if not emp_id and filters.employee_name:
            emp = Employee.query.filter_by(employee_name=filters.employee_name).first()
            emp_id = emp.employee_id if emp else None
        participation = _employee_participation_roles(emp_id) if emp_id else {}
        if not participation:
            return _empty_result()

    query = apply_ticket_scope(_ticket_base_query(), context)
    query = _apply_ticket_filters(query, filters)
    if participation is not None:
        query = query.filter(Ticket.ticket_id.in_(set(participation.keys())))
    total_count = query.order_by(None).count()
    query = query.order_by(Ticket.sla_deadline.asc(), Ticket.create_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_tickets")]
    tickets = query.all()
    rows = []
    for ticket in tickets:
        row = mask_sensitive_fields(_ticket_to_row(ticket), context)
        if participation is not None:
            row["participation_roles"] = "、".join(sorted(participation.get(ticket.ticket_id, set()))) or "-"
        rows.append(row)
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def _ticket_detail_row(ticket: Ticket) -> dict:
    latest_log = ticket.action_logs[0] if ticket.action_logs else None
    feedback = ticket.feedback
    return {
        **_ticket_to_row(ticket),
        "complaint_content": ticket.complaint.complaint_content,
        "complaint_time": ticket.complaint.complaint_time.strftime("%Y-%m-%d %H:%M:%S"),
        "order_status": ticket.complaint.order.order_status,
        "order_amount": float(ticket.complaint.order.order_amount),
        "assignment_count": len(ticket.assignments),
        "escalation_count": len(ticket.escalations),
        "action_log_count": len(ticket.action_logs),
        "latest_action_type": latest_log.action_type if latest_log else "-",
        "action_summary": latest_log.action_content if latest_log else "-",
        "satisfaction_score": feedback.satisfaction_score if feedback else None,
        "feedback_score": feedback.satisfaction_score if feedback else None,
        "feedback_time": feedback.feedback_time.strftime("%Y-%m-%d %H:%M:%S") if feedback else "-",
        "feedback_content": feedback.feedback_content if feedback else "-",
    }


def get_ticket_detail(context, ticket_id: str):
    ticket_ids = _expand_ticket_ids(ticket_id)
    if not ticket_ids:
        return _empty_result()

    query = apply_ticket_scope(_ticket_base_query(), context).filter(Ticket.ticket_id.in_(ticket_ids))
    sql_debug = [_compile_query_sql(query, "get_ticket_detail")]
    tickets = query.all()
    if not tickets:
        return _empty_result()

    rows = [mask_sensitive_fields(_ticket_detail_row(ticket), context) for ticket in tickets]
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_complaints(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    if filters.query_kind == "complaint_type_rules":
        return query_complaint_type_rules(context, filters)
    if filters.query_kind == "conversion_consistency_audit":
        return query_conversion_consistency_audit(context, filters)
    query = (
        Complaint.query.join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .join(RideOrder, Complaint.order_id == RideOrder.order_id)
        .join(Passenger, Complaint.passenger_id == Passenger.passenger_id)
        .options(
            joinedload(Complaint.complaint_type).joinedload(ComplaintType.default_department),
            joinedload(Complaint.ticket).joinedload(Ticket.department),
            joinedload(Complaint.order).joinedload(RideOrder.passenger),
            joinedload(Complaint.order).joinedload(RideOrder.driver),
        )
    )
    query = apply_ticket_scope(query, context)
    if filters.complaint_id:
        query = query.filter(Complaint.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(Complaint.order_id == filters.order_id)
    if filters.driver_id:
        query = query.filter(RideOrder.driver_id == filters.driver_id)
    if filters.passenger_id:
        query = query.filter(Complaint.passenger_id == filters.passenger_id)
    if filters.complaint_type:
        query = query.filter(ComplaintType.type_name == filters.complaint_type)
    if filters.urgency_level:
        query = query.filter(Complaint.urgency_level == filters.urgency_level)
    if filters.complaint_status:
        if filters.complaint_status in {"待处理", "未关闭"}:
            query = query.filter(Complaint.complaint_status != "已关闭")
        else:
            query = query.filter(Complaint.complaint_status == filters.complaint_status)
    if filters.ticket_status:
        if filters.ticket_status == "未关闭":
            query = query.filter(Ticket.ticket_status != "已关闭")
        else:
            query = query.filter(Ticket.ticket_status == filters.ticket_status)
    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(Complaint.complaint_time >= start_dt)
    if end_dt:
        query = query.filter(Complaint.complaint_time < end_dt)

    total_count = query.order_by(None).count()
    query = query.order_by(Complaint.complaint_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_complaints")]
    complaints = query.all()
    rows = []
    for complaint in complaints:
        row = {
            "complaint_id": complaint.complaint_id,
            "order_id": complaint.order_id,
            "ticket_id": complaint.ticket.ticket_id if complaint.ticket else "-",
            "ticket_status": complaint.ticket.ticket_status if complaint.ticket else "-",
            "complaint_type": complaint.complaint_type.type_name if complaint.complaint_type else "-",
            "default_department_name": complaint.complaint_type.default_department.department_name if complaint.complaint_type and complaint.complaint_type.default_department else "-",
            "default_sla_hours": complaint.complaint_type.default_sla_hours if complaint.complaint_type else "-",
            "default_priority_level": complaint.complaint_type.default_priority_level if complaint.complaint_type else "-",
            "complaint_status": complaint.complaint_status,
            "urgency_level": complaint.urgency_level,
            "department_name": complaint.ticket.department.department_name if complaint.ticket and complaint.ticket.department else "-",
            "complaint_content": complaint.complaint_content,
            "complaint_time": complaint.complaint_time.strftime("%Y-%m-%d %H:%M:%S"),
            "passenger_name": complaint.passenger.passenger_name if complaint.passenger else "-",
            "passenger_phone": complaint.passenger.phone if complaint.passenger else "-",
            "driver_name": complaint.order.driver.driver_name if complaint.order and complaint.order.driver else "-",
            "driver_phone": complaint.order.driver.phone if complaint.order and complaint.order.driver else "-",
        }
        rows.append(mask_sensitive_fields(row, context))
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_orders(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    if filters.query_kind == "driver_order_stats":
        return query_driver_order_stats(context, filters)
    if filters.query_kind == "driver_service_risk":
        return query_driver_service_risk(context, filters)
    if filters.query_kind == "high_value_order_risk":
        return query_high_value_order_risk(context, filters)
    if context.role in {"admin", "manager", "customer_service"}:
        query = RideOrder.query.options(
            joinedload(RideOrder.passenger),
            joinedload(RideOrder.driver),
            joinedload(RideOrder.complaints),
        )
    else:
        query = (
            RideOrder.query.join(Complaint, Complaint.order_id == RideOrder.order_id)
            .join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
            .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
            .join(Department, Ticket.department_id == Department.department_id)
            .options(
                joinedload(RideOrder.passenger),
                joinedload(RideOrder.driver),
                joinedload(RideOrder.complaints),
            )
            .distinct()
        )
        query = apply_ticket_scope(query, context)

    if filters.order_id:
        query = query.filter(RideOrder.order_id == filters.order_id)
    if filters.passenger_id:
        query = query.filter(RideOrder.passenger_id == filters.passenger_id)
    if filters.driver_id:
        query = query.filter(RideOrder.driver_id == filters.driver_id)
    if filters.order_status:
        query = query.filter(RideOrder.order_status == filters.order_status)
    if filters.min_amount is not None:
        query = query.filter(RideOrder.order_amount >= filters.min_amount)
    if filters.max_amount is not None:
        query = query.filter(RideOrder.order_amount <= filters.max_amount)

    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(RideOrder.order_time >= start_dt)
    if end_dt:
        query = query.filter(RideOrder.order_time < end_dt)

    total_count = query.order_by(None).count()
    query = query.order_by(RideOrder.order_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_orders")]
    orders = query.all()
    rows = []
    for order in orders:
        row = {
            "order_id": order.order_id,
            "passenger_name": order.passenger.passenger_name if order.passenger else "-",
            "passenger_phone": order.passenger.phone if order.passenger else "-",
            "driver_name": order.driver.driver_name if order.driver else "-",
            "driver_phone": order.driver.phone if order.driver else "-",
            "start_location": order.start_location,
            "end_location": order.end_location,
            "order_amount": float(order.order_amount),
            "order_status": order.order_status,
            "order_time": order.order_time.strftime("%Y-%m-%d %H:%M:%S"),
            "finish_time": order.finish_time.strftime("%Y-%m-%d %H:%M:%S") if order.finish_time else "-",
            "complaint_count": len(order.complaints),
        }
        rows.append(mask_sensitive_fields(row, context))
    if filters.query_kind == "order_complaint_check" and rows:
        first = rows[0]
        first["has_complaint"] = first["complaint_count"] > 0
        rows = [first]
    return {
        "rows": rows,
        "data_count": total_count if filters.query_kind != "order_complaint_check" else len(rows),
        "risk_flags": _derive_risk_flags(rows),
        "sql_debug": sql_debug,
    }


def query_dashboard_summary(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    if context.role == "employee":
        return _empty_result()

    if filters.query_kind == "department_health":
        return query_department_health(context, filters)
    if filters.query_kind == "department_performance":
        return query_department_performance(context, filters)
    if filters.query_kind == "sla_risk_scan":
        return query_sla_risk_scan(context, filters)
    if filters.query_kind == "complaint_type_quality":
        return query_complaint_type_quality(context, filters)
    if filters.query_kind == "employee_efficiency_anomaly":
        return query_employee_efficiency_anomaly(context, filters)
    if filters.query_kind == "escalation_effectiveness":
        return query_escalation_effectiveness(context, filters)
    if filters.query_kind == "customer_service_balance":
        return query_customer_service_balance(context, filters)
    if filters.query_kind == "system_health_report":
        return query_system_health_report(context, filters)

    now_dt = datetime.now()
    query = (
        db.session.query(
            Department.department_name.label("department_name"),
            func.count(Ticket.ticket_id).label("total_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status.in_(tuple(PROCESSING_STATUSES)), 1), else_=0)),
                0,
            ).label("processing_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status == "已关闭", 1), else_=0)),
                0,
            ).label("closed_tickets"),
            func.coalesce(
                func.sum(
                    case(
                        (((Ticket.ticket_status != "已关闭") & (Ticket.sla_deadline < now_dt)), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("overdue_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status == "待反馈", 1), else_=0)),
                0,
            ).label("pending_feedback_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status == "已升级", 1), else_=0)),
                0,
            ).label("escalated_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status == "已重开", 1), else_=0)),
                0,
            ).label("reopened_tickets"),
            func.coalesce(
                func.sum(case((Ticket.ticket_status == "待分派", 1), else_=0)),
                0,
            ).label("unassigned_tickets"),
        )
        .outerjoin(Ticket, Ticket.department_id == Department.department_id)
        .group_by(Department.department_id, Department.department_name)
    )

    allowed_departments = get_allowed_dashboard_departments(context)
    if allowed_departments:
        query = query.filter(Department.department_name.in_(tuple(allowed_departments)))

    if filters.department_name:
        query = query.filter(Department.department_name == filters.department_name)

    if filters.is_overdue:
        query = query.order_by(func.coalesce(func.sum(case((((Ticket.ticket_status != "已关闭") & (Ticket.sla_deadline < now_dt)), 1), else_=0)), 0).desc(), Department.department_name.asc())
    else:
        query = query.order_by(Department.department_name.asc())

    query = query.limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_dashboard_summary")]
    summary_rows = query.all()
    rows = [
        {
            "department_name": item.department_name,
            "total_tickets": int(item.total_tickets or 0),
            "processing_tickets": int(item.processing_tickets or 0),
            "closed_tickets": int(item.closed_tickets or 0),
            "overdue_tickets": int(item.overdue_tickets or 0),
            "pending_feedback_tickets": int(item.pending_feedback_tickets or 0),
            "escalated_tickets": int(item.escalated_tickets or 0),
            "reopened_tickets": int(item.reopened_tickets or 0),
            "unassigned_tickets": int(item.unassigned_tickets or 0),
        }
        for item in summary_rows
    ]
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def _iter_allowed_departments(context, filters: QueryFilters):
    query = Department.query.order_by(Department.department_name.asc())
    allowed_departments = get_allowed_dashboard_departments(context)
    if allowed_departments:
        query = query.filter(Department.department_name.in_(tuple(allowed_departments)))
    if filters.department_name:
        query = query.filter(Department.department_name == filters.department_name)
    return query.all()


def _avg(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 2) if values else 0.0


def _ticket_handle_hours(ticket: Ticket, now_dt: datetime | None = None) -> float:
    end_time = ticket.close_time or now_dt or datetime.now()
    return round((end_time - ticket.create_time).total_seconds() / 3600, 2)


def _ticket_complaint_type(ticket: Ticket) -> str:
    if ticket.complaint and ticket.complaint.complaint_type:
        return ticket.complaint.complaint_type.type_name
    return "-"


def query_department_health(context, filters: QueryFilters):
    departments = _iter_allowed_departments(context, filters)
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    if filters.department_name:
        ticket_query = ticket_query.filter(Department.department_name == filters.department_name)
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_department_health")]

    tickets_by_department = {}
    now_dt = datetime.now()
    for ticket in tickets:
        dept_name = ticket.department.department_name if ticket.department else "未分配部门"
        tickets_by_department.setdefault(dept_name, []).append(ticket)

    rows = []
    for department in departments:
        dept_tickets = tickets_by_department.get(department.department_name, [])
        open_tickets = [ticket for ticket in dept_tickets if ticket.ticket_status != "已关闭"]
        overdue_tickets = [ticket for ticket in open_tickets if ticket.sla_deadline < now_dt]
        closed_tickets = [ticket for ticket in dept_tickets if ticket.ticket_status == "已关闭" and ticket.close_time]
        handled_tickets = closed_tickets or dept_tickets
        durations = []
        for ticket in handled_tickets:
            end_time = ticket.close_time or now_dt
            durations.append((end_time - ticket.create_time).total_seconds() / 3600)
        avg_handle_hours = round(sum(durations) / len(durations), 2) if durations else 0.0
        overdue_ratio = round((len(overdue_tickets) / len(open_tickets)) * 100, 2) if open_tickets else 0.0
        p1_count = sum(1 for ticket in dept_tickets if ticket.priority_level == "P1")
        p2_count = sum(1 for ticket in dept_tickets if ticket.priority_level == "P2")
        p3_count = sum(1 for ticket in dept_tickets if ticket.priority_level == "P3")

        risk_score = 0
        if len(overdue_tickets) >= 3 or overdue_ratio >= 50:
            risk_score += 2
        elif len(overdue_tickets) >= 1 or overdue_ratio >= 20:
            risk_score += 1
        if p1_count >= 2:
            risk_score += 2
        elif p1_count >= 1:
            risk_score += 1
        if any(ticket.ticket_status in {"已升级", "已重开", "待反馈"} for ticket in dept_tickets):
            risk_score += 1
        risk_rating = "高" if risk_score >= 4 else "中" if risk_score >= 2 else "低"

        rows.append(
            {
                "department_name": department.department_name,
                "open_ticket_count": len(open_tickets),
                "overdue_ticket_count": len(overdue_tickets),
                "overdue_ratio": overdue_ratio,
                "avg_handle_hours": avg_handle_hours,
                "p1_count": p1_count,
                "p2_count": p2_count,
                "p3_count": p3_count,
                "risk_rating": risk_rating,
            }
        )

    rows.sort(key=lambda row: ({"高": 0, "中": 1, "低": 2}.get(row["risk_rating"], 3), -row["open_ticket_count"], row["department_name"]))
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_department_performance(context, filters: QueryFilters):
    departments = _iter_allowed_departments(context, filters)
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    if filters.department_name:
        ticket_query = ticket_query.filter(Department.department_name == filters.department_name)
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_department_performance")]

    tickets_by_department = {}
    now_dt = datetime.now()
    for ticket in tickets:
        dept_name = ticket.department.department_name if ticket.department else "未分配部门"
        tickets_by_department.setdefault(dept_name, []).append(ticket)

    rows = []
    for department in departments:
        dept_tickets = tickets_by_department.get(department.department_name, [])
        closed_tickets = [ticket for ticket in dept_tickets if ticket.ticket_status == "已关闭" and ticket.close_time]
        processing_count = sum(1 for ticket in dept_tickets if ticket.ticket_status in PROCESSING_STATUSES)
        close_durations = [
            (ticket.close_time - ticket.create_time).total_seconds() / 3600
            for ticket in closed_tickets
            if ticket.close_time
        ]
        avg_close_hours = round(sum(close_durations) / len(close_durations), 2) if close_durations else 0.0
        overdue_count = sum(1 for ticket in dept_tickets if ticket.ticket_status != "已关闭" and ticket.sla_deadline < now_dt)
        overdue_rate = round((overdue_count / len(dept_tickets)) * 100, 2) if dept_tickets else 0.0
        scores = [ticket.feedback.satisfaction_score for ticket in dept_tickets if ticket.feedback]
        avg_satisfaction_score = round(sum(scores) / len(scores), 2) if scores else 0.0

        score = 100.0
        score -= min(overdue_rate, 100) * 0.5
        score -= min(avg_close_hours, 240) * 0.1
        score += avg_satisfaction_score * 5
        score += min(processing_count, 20) * 0.2
        score = round(score, 2)

        rows.append(
            {
                "department_name": department.department_name,
                "processing_count": processing_count,
                "avg_close_hours": avg_close_hours,
                "overdue_rate": overdue_rate,
                "avg_satisfaction_score": avg_satisfaction_score,
                "performance_score": score,
            }
        )

    rows.sort(key=lambda row: (-row["performance_score"], row["department_name"]))
    for index, row in enumerate(rows, start=1):
        row["performance_rank"] = index
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_sla_risk_scan(context, filters: QueryFilters):
    departments = _iter_allowed_departments(context, filters)
    ticket_query = apply_ticket_scope(_ticket_base_query(), context).filter(Ticket.ticket_status != "已关闭")
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_sla_risk_scan")]
    now_dt = datetime.now()
    by_department: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        dept_name = ticket.department.department_name if ticket.department else "未分配部门"
        by_department.setdefault(dept_name, []).append(ticket)

    rows = []
    for department in departments:
        dept_tickets = by_department.get(department.department_name, [])
        due_within_6h = sum(
            1
            for ticket in dept_tickets
            if 0 <= (ticket.sla_deadline - now_dt).total_seconds() / 3600 < 6
        )
        overdue_open = sum(1 for ticket in dept_tickets if ticket.sla_deadline < now_dt)
        overdue_escalated = sum(
            1
            for ticket in dept_tickets
            if ticket.sla_deadline < now_dt and (ticket.ticket_status == "已升级" or bool(ticket.escalations))
        )
        risk_distribution = round(((due_within_6h + overdue_open) / len(dept_tickets)) * 100, 2) if dept_tickets else 0.0
        rows.append(
            {
                "department_name": department.department_name,
                "open_ticket_count": len(dept_tickets),
                "due_within_6h_count": due_within_6h,
                "overdue_open_count": overdue_open,
                "overdue_escalated_count": overdue_escalated,
                "risk_distribution_pct": risk_distribution,
            }
        )

    rows.sort(key=lambda row: (-row["overdue_open_count"], -row["due_within_6h_count"], row["department_name"]))
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_complaint_type_quality(context, filters: QueryFilters):
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_complaint_type_quality")]
    now_dt = datetime.now()
    by_type: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        by_type.setdefault(_ticket_complaint_type(ticket), []).append(ticket)

    rows = []
    for complaint_type, type_tickets in by_type.items():
        handle_hours = [_ticket_handle_hours(ticket, now_dt) for ticket in type_tickets]
        scores = [ticket.feedback.satisfaction_score for ticket in type_tickets if ticket.feedback]
        escalation_rate = round((sum(1 for ticket in type_tickets if ticket.escalations) / len(type_tickets)) * 100, 2)
        difficulty_score = round(_avg(handle_hours) * 0.4 + (5 - _avg(scores or [3])) * 10 + escalation_rate * 0.5, 2)
        rows.append(
            {
                "complaint_type": complaint_type,
                "ticket_count": len(type_tickets),
                "avg_handle_hours": _avg(handle_hours),
                "avg_satisfaction_score": _avg(scores),
                "escalation_rate": escalation_rate,
                "difficulty_score": difficulty_score,
            }
        )

    rows.sort(key=lambda row: (-row["difficulty_score"], -row["ticket_count"], row["complaint_type"]))
    for index, row in enumerate(rows, start=1):
        row["difficulty_rank"] = index
        row["is_top3_hardest"] = index <= 3
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_employee_efficiency_anomaly(context, filters: QueryFilters):
    ticket_query = apply_ticket_scope(_ticket_base_query(), context).filter(Ticket.current_owner_id.isnot(None))
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_employee_efficiency_anomaly")]
    now_dt = datetime.now()
    by_employee: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        if ticket.current_owner:
            by_employee.setdefault(ticket.current_owner.employee_name, []).append(ticket)

    base_counts = [len(items) for items in by_employee.values()]
    overall_avg_count = _avg(base_counts)
    rows = []
    for employee_name, emp_tickets in by_employee.items():
        processed_count = len(emp_tickets)
        scores = [ticket.feedback.satisfaction_score for ticket in emp_tickets if ticket.feedback]
        avg_satisfaction = _avg(scores)
        overdue_count = sum(1 for ticket in emp_tickets if ticket.ticket_status != "已关闭" and ticket.sla_deadline < now_dt)
        overdue_rate = round((overdue_count / processed_count) * 100, 2) if processed_count else 0.0

        anomaly_type = None
        if processed_count >= max(3, overall_avg_count * 1.5) and avg_satisfaction and avg_satisfaction < 3.5:
            anomaly_type = "高负载低满意度"
        elif processed_count <= max(1, overall_avg_count * 0.5) and overdue_rate >= 50:
            anomaly_type = "低产出高超时"

        if anomaly_type:
            rows.append(
                {
                    "employee_name": employee_name,
                    "processed_ticket_count": processed_count,
                    "avg_satisfaction_score": avg_satisfaction,
                    "overdue_rate": overdue_rate,
                    "anomaly_type": anomaly_type,
                }
            )

    rows.sort(key=lambda row: (-row["overdue_rate"], row["avg_satisfaction_score"], -row["processed_ticket_count"], row["employee_name"]))
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_escalation_effectiveness(context, filters: QueryFilters):
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_escalation_effectiveness")]
    escalated_tickets = [ticket for ticket in tickets if ticket.escalations]
    non_escalated_tickets = [ticket for ticket in tickets if not ticket.escalations and ticket.feedback]

    path_metrics: dict[str, list[float]] = {}
    department_counts: dict[str, int] = {}
    escalated_scores = []
    for ticket in escalated_tickets:
        dept_name = ticket.department.department_name if ticket.department else "未分配部门"
        department_counts[dept_name] = department_counts.get(dept_name, 0) + len(ticket.escalations)
        for escalation in ticket.escalations:
            path_name = f"{escalation.from_level}->{escalation.to_level}"
            path_metrics.setdefault(path_name, []).append(
                round((escalation.escalation_time - ticket.create_time).total_seconds() / 3600, 2)
            )
        if ticket.feedback:
            escalated_scores.append(ticket.feedback.satisfaction_score)

    baseline_scores = [ticket.feedback.satisfaction_score for ticket in non_escalated_tickets]
    summary_row = {
        "row_type": "escalation_effectiveness_summary",
        "avg_escalation_hours": _avg([value for values in path_metrics.values() for value in values]),
        "avg_escalated_satisfaction": _avg(escalated_scores),
        "avg_non_escalated_satisfaction": _avg(baseline_scores),
        "most_frequent_department": max(department_counts.items(), key=lambda item: item[1])[0] if department_counts else "-",
        "is_effective": _avg(escalated_scores) >= _avg(baseline_scores) if escalated_scores and baseline_scores else False,
    }
    rows = [summary_row]
    for path_name, values in sorted(path_metrics.items(), key=lambda item: (-len(item[1]), item[0])):
        rows.append(
            {
                "row_type": "escalation_path",
                "path_name": path_name,
                "avg_path_hours": _avg(values),
                "escalation_count": len(values),
            }
        )
    for department_name, count in sorted(department_counts.items(), key=lambda item: (-item[1], item[0])):
        rows.append(
            {
                "row_type": "department_frequency",
                "department_name": department_name,
                "escalation_count": count,
            }
        )
    return {"rows": rows, "data_count": len(escalated_tickets), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_customer_service_balance(context, filters: QueryFilters):
    filters = filters.model_copy(deep=True)
    filters.department_name = "客服部"
    employees = (
        Employee.query.join(Department, Employee.department_id == Department.department_id)
        .filter(Department.department_name == "客服部")
        .all()
    )
    ticket_query = apply_ticket_scope(_ticket_base_query(), context).filter(
        Department.department_name == "客服部",
        Ticket.ticket_status != "已关闭",
    )
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_customer_service_balance")]

    loads = []
    by_owner = {}
    for ticket in tickets:
        owner = ticket.current_owner.employee_name if ticket.current_owner else "未分派"
        by_owner[owner] = by_owner.get(owner, 0) + 1
    for employee in employees:
        loads.append({"employee_name": employee.employee_name, "open_ticket_load": by_owner.get(employee.employee_name, 0)})

    loads.sort(key=lambda row: (-row["open_ticket_load"], row["employee_name"]))
    top_count = max(1, int(len(loads) * 0.1 + 0.9999)) if loads else 0
    avg_load = _avg([row["open_ticket_load"] for row in loads])
    severe_imbalance = bool(loads and loads[0]["open_ticket_load"] >= max(2, avg_load * 2))
    summary_row = {
        "row_type": "cs_balance_summary",
        "department_name": "客服部",
        "open_ticket_count": len(tickets),
        "employee_count": len(employees),
        "avg_employee_load": avg_load,
        "top_load_threshold_count": top_count,
        "severe_imbalance": severe_imbalance,
    }
    rows = [summary_row]
    for index, row in enumerate(loads, start=1):
        rows.append(
            {
                "row_type": "employee_load",
                "employee_name": row["employee_name"],
                "open_ticket_load": row["open_ticket_load"],
                "is_top_10_percent": index <= top_count,
            }
        )
    return {"rows": rows, "data_count": len(loads), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_system_health_report(context, filters: QueryFilters):
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    tickets = ticket_query.all()
    sql_debug = [_compile_query_sql(ticket_query, "query_system_health_report")]
    now_dt = datetime.now()
    today_start = datetime(now_dt.year, now_dt.month, now_dt.day)
    week_start = today_start - timedelta(days=7)
    prev_week_start = week_start - timedelta(days=7)

    visible_complaints = {ticket.complaint_id: ticket.complaint for ticket in tickets if ticket.complaint}
    new_complaints_today = sum(1 for complaint in visible_complaints.values() if complaint.complaint_time >= today_start)
    new_tickets_today = sum(1 for ticket in tickets if ticket.create_time >= today_start)
    closed_today = sum(1 for ticket in tickets if ticket.close_time and ticket.close_time >= today_start)
    close_rate = round((closed_today / new_tickets_today) * 100, 2) if new_tickets_today else 0.0

    current_closed = [ticket for ticket in tickets if ticket.close_time and ticket.close_time >= week_start]
    previous_closed = [ticket for ticket in tickets if ticket.close_time and prev_week_start <= ticket.close_time < week_start]
    current_avg_handle = _avg([_ticket_handle_hours(ticket, ticket.close_time) for ticket in current_closed])
    previous_avg_handle = _avg([_ticket_handle_hours(ticket, ticket.close_time) for ticket in previous_closed])
    handle_trend = round(current_avg_handle - previous_avg_handle, 2)

    current_week_tickets = [ticket for ticket in tickets if ticket.create_time >= week_start]
    previous_week_tickets = [ticket for ticket in tickets if prev_week_start <= ticket.create_time < week_start]
    current_overdue_rate = round(
        (sum(1 for ticket in current_week_tickets if ticket.ticket_status != "已关闭" and ticket.sla_deadline < now_dt) / len(current_week_tickets)) * 100,
        2,
    ) if current_week_tickets else 0.0
    previous_overdue_rate = round(
        (sum(1 for ticket in previous_week_tickets if ticket.ticket_status != "已关闭" and ticket.sla_deadline < now_dt) / len(previous_week_tickets)) * 100,
        2,
    ) if previous_week_tickets else 0.0
    overdue_trend = round(current_overdue_rate - previous_overdue_rate, 2)

    scores = [ticket.feedback.satisfaction_score for ticket in tickets if ticket.feedback and ticket.feedback.feedback_time >= week_start]
    distribution = {score: scores.count(score) for score in range(1, 6)}
    if current_overdue_rate >= 40 or (scores and _avg(scores) < 3):
        health_level = "Critical"
    elif current_overdue_rate >= 20 or handle_trend > 12:
        health_level = "Warning"
    else:
        health_level = "Healthy"

    rows = [
        {
            "row_type": "system_health_summary",
            "new_complaints_today": new_complaints_today,
            "new_tickets_today": new_tickets_today,
            "closed_today": closed_today,
            "close_rate_pct": close_rate,
            "avg_handle_hours_current_7d": current_avg_handle,
            "avg_handle_hours_trend": handle_trend,
            "sla_overdue_rate_current_7d": current_overdue_rate,
            "sla_overdue_rate_trend": overdue_trend,
            "satisfaction_distribution": " / ".join(f"{score}星:{count}" for score, count in distribution.items()),
            "health_level": health_level,
        }
    ]
    return {"rows": rows, "data_count": 1, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_low_feedback_risk_analysis(context, filters: QueryFilters):
    ticket_query = apply_ticket_scope(_ticket_base_query(), context)
    tickets = [ticket for ticket in ticket_query.all() if ticket.feedback and ticket.feedback.satisfaction_score <= (filters.score_below or 3)]
    sql_debug = [_compile_query_sql(ticket_query, "query_low_feedback_risk_analysis")]

    complaint_type_counts = {}
    escalated_count = 0
    overdue_count = 0
    total_logs = 0
    high_risk_rows = []
    now_dt = datetime.now()

    for ticket in tickets:
        complaint_type = ticket.complaint.complaint_type.type_name if ticket.complaint and ticket.complaint.complaint_type else "-"
        complaint_type_counts[complaint_type] = complaint_type_counts.get(complaint_type, 0) + 1
        if ticket.escalations:
            escalated_count += 1
        if ticket.ticket_status != "已关闭" and ticket.sla_deadline < now_dt:
            overdue_count += 1
        total_logs += len(ticket.action_logs)
        if ticket.ticket_status != "已关闭":
            row = _ticket_to_row(ticket)
            row.update(
                {
                    "row_type": "high_risk_ticket",
                    "satisfaction_score": ticket.feedback.satisfaction_score,
                    "action_log_count": len(ticket.action_logs),
                    "has_escalation": bool(ticket.escalations),
                }
            )
            high_risk_rows.append(mask_sensitive_fields(row, context))

    distribution = "、".join(
        f"{name}:{count}" for name, count in sorted(complaint_type_counts.items(), key=lambda item: (-item[1], item[0]))
    ) or "-"
    avg_action_log_count = round(total_logs / len(tickets), 2) if tickets else 0.0

    summary_row = {
        "row_type": "analysis_summary",
        "low_feedback_ticket_count": len(tickets),
        "complaint_type_distribution": distribution,
        "escalated_ticket_count": escalated_count,
        "avg_action_log_count": avg_action_log_count,
        "overdue_ticket_count": overdue_count,
        "high_risk_unclosed_count": len(high_risk_rows),
    }

    rows = [summary_row] + high_risk_rows
    return {"rows": rows, "data_count": len(tickets), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_conversion_consistency_audit(context, filters: QueryFilters):
    visible_tickets = apply_ticket_scope(_ticket_base_query(), context).all()
    visible_ticket_ids = {ticket.ticket_id for ticket in visible_tickets}
    visible_complaint_ids = {ticket.complaint_id for ticket in visible_tickets}
    complaint_counts = (
        db.session.query(Ticket.complaint_id, func.count(Ticket.ticket_id).label("ticket_count"))
        .filter(Ticket.complaint_id.in_(tuple(visible_complaint_ids)) if visible_complaint_ids else False)
        .group_by(Ticket.complaint_id)
        .all()
    )
    complaint_count_map = {item.complaint_id: int(item.ticket_count or 0) for item in complaint_counts}
    multi_ticket_issues = [
        {"row_type": "multi_ticket_complaint", "complaint_id": complaint_id, "ticket_count": count}
        for complaint_id, count in complaint_count_map.items()
        if count > 1
    ]
    orphan_tickets = (
        db.session.query(Ticket.ticket_id)
        .outerjoin(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .filter(Complaint.complaint_id.is_(None), Ticket.ticket_id.in_(tuple(visible_ticket_ids)) if visible_ticket_ids else False)
        .all()
    )
    complaints_without_ticket = (
        db.session.query(Complaint.complaint_id)
        .outerjoin(Ticket, Ticket.complaint_id == Complaint.complaint_id)
        .filter(Ticket.ticket_id.is_(None), Complaint.complaint_id.in_(tuple(visible_complaint_ids)) if visible_complaint_ids else False)
        .all()
    )
    rows = [
        {
            "row_type": "consistency_summary",
            "multi_ticket_complaint_count": len(multi_ticket_issues),
            "orphan_ticket_count": len(orphan_tickets),
            "complaints_without_ticket_count": len(complaints_without_ticket),
        }
    ]
    rows.extend(multi_ticket_issues)
    rows.extend({"row_type": "orphan_ticket", "ticket_id": item.ticket_id} for item in orphan_tickets)
    rows.extend({"row_type": "complaint_without_ticket", "complaint_id": item.complaint_id} for item in complaints_without_ticket)
    query = db.session.query(Complaint.complaint_id).outerjoin(Ticket, Ticket.complaint_id == Complaint.complaint_id)
    sql_debug = [_compile_query_sql(query, "query_conversion_consistency_audit")]
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_driver_service_risk(context, filters: QueryFilters):
    if context.role in {"admin", "manager", "customer_service"}:
        order_query = RideOrder.query.options(
            joinedload(RideOrder.driver),
            joinedload(RideOrder.complaints).joinedload(Complaint.ticket).joinedload(Ticket.feedback),
            joinedload(RideOrder.complaints).joinedload(Complaint.ticket).joinedload(Ticket.department),
        )
    else:
        order_query = (
            RideOrder.query.join(Complaint, Complaint.order_id == RideOrder.order_id)
            .join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
            .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
            .join(Department, Ticket.department_id == Department.department_id)
            .options(
                joinedload(RideOrder.driver),
                joinedload(RideOrder.complaints).joinedload(Complaint.ticket).joinedload(Ticket.feedback),
            )
            .distinct()
        )
        order_query = apply_ticket_scope(order_query, context)
    orders = order_query.all()
    sql_debug = [_compile_query_sql(order_query, "query_driver_service_risk")]

    by_driver = {}
    for order in orders:
        if not order.driver:
            continue
        entry = by_driver.setdefault(
            order.driver.driver_id,
            {
                "driver_id": order.driver.driver_id,
                "driver_name": order.driver.driver_name,
                "complaint_count": 0,
                "scores": [],
                "p1_ticket_count": 0,
            },
        )
        complaint_count = len(order.complaints)
        entry["complaint_count"] += complaint_count
        for complaint in order.complaints:
            if complaint.ticket and complaint.ticket.feedback:
                entry["scores"].append(complaint.ticket.feedback.satisfaction_score)
            if complaint.ticket and complaint.ticket.priority_level == "P1":
                entry["p1_ticket_count"] += 1

    rows = []
    for item in by_driver.values():
        if item["complaint_count"] < 3:
            continue
        avg_satisfaction = _avg(item["scores"])
        risk_level = "高" if item["p1_ticket_count"] > 0 or item["complaint_count"] >= 5 else "中"
        rows.append(
            {
                "driver_id": item["driver_id"],
                "driver_name": item["driver_name"],
                "driver_complaint_count": item["complaint_count"],
                "avg_satisfaction_score": avg_satisfaction,
                "p1_ticket_count": item["p1_ticket_count"],
                "is_high_priority_involved": item["p1_ticket_count"] > 0,
                "risk_level": risk_level,
            }
        )
    rows.sort(key=lambda row: (-row["driver_complaint_count"], row["avg_satisfaction_score"], row["driver_name"]))
    return {"rows": rows, "data_count": len(rows), "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_high_value_order_risk(context, filters: QueryFilters):
    if context.role in {"admin", "manager", "customer_service"}:
        order_query = RideOrder.query.options(
            joinedload(RideOrder.complaints).joinedload(Complaint.ticket).joinedload(Ticket.feedback),
            joinedload(RideOrder.passenger),
            joinedload(RideOrder.driver),
        )
    else:
        order_query = (
            RideOrder.query.join(Complaint, Complaint.order_id == RideOrder.order_id)
            .join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
            .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
            .join(Department, Ticket.department_id == Department.department_id)
            .options(joinedload(RideOrder.complaints).joinedload(Complaint.ticket).joinedload(Ticket.feedback))
            .distinct()
        )
        order_query = apply_ticket_scope(order_query, context)
    orders = order_query.order_by(RideOrder.order_amount.desc()).all()
    sql_debug = [_compile_query_sql(order_query, "query_high_value_order_risk")]
    if not orders:
        return {"rows": [], "data_count": 0, "risk_flags": [], "sql_debug": sql_debug}

    top_count = max(1, int(len(orders) * 0.1 + 0.9999))
    high_value_orders = orders[:top_count]
    now_dt = datetime.now()
    complaint_orders = [order for order in high_value_orders if order.complaints]
    overdue_orders = [
        order
        for order in high_value_orders
        if any(
            complaint.ticket and complaint.ticket.ticket_status != "已关闭" and complaint.ticket.sla_deadline < now_dt
            for complaint in order.complaints
        )
    ]
    scores = [
        complaint.ticket.feedback.satisfaction_score
        for order in high_value_orders
        for complaint in order.complaints
        if complaint.ticket and complaint.ticket.feedback
    ]
    complaint_rate = round((len(complaint_orders) / len(high_value_orders)) * 100, 2)
    overdue_rate = round((len(overdue_orders) / len(high_value_orders)) * 100, 2)
    avg_satisfaction = _avg(scores)
    risk_rating = "高" if complaint_rate >= 50 or overdue_rate >= 30 or (scores and avg_satisfaction < 3.5) else "中" if complaint_rate >= 20 else "低"
    rows = [
        {
            "row_type": "high_value_order_summary",
            "top_order_count": len(high_value_orders),
            "complaint_rate": complaint_rate,
            "sla_overdue_rate": overdue_rate,
            "avg_satisfaction_score": avg_satisfaction,
            "risk_rating": risk_rating,
        }
    ]
    return {"rows": rows, "data_count": 1, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_ticket_risk_scoring(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    query = apply_ticket_scope(_ticket_base_query(), context)
    query = _apply_ticket_filters(query, filters)
    tickets = query.all()
    sql_debug = [_compile_query_sql(query, "query_ticket_risk_scoring")]
    now_dt = datetime.now()
    complaint_weights = {
        "安全事件": 25,
        "费用争议": 12,
        "司机服务": 10,
        "取消争议": 8,
        "物品遗失": 6,
        "平台异常": 9,
        "其他问题": 5,
    }
    rows = []
    for ticket in tickets:
        remaining_hours = round((ticket.sla_deadline - now_dt).total_seconds() / 3600, 2)
        sla_component = 40 if remaining_hours < 0 else 30 if remaining_hours < 6 else 15 if remaining_hours < 24 else 5
        escalation_component = 15 if (ticket.ticket_status == "已升级" or ticket.escalations) else 0
        complaint_component = complaint_weights.get(_ticket_complaint_type(ticket), 5)
        score_component = 0
        current_satisfaction = None
        if ticket.feedback:
            current_satisfaction = ticket.feedback.satisfaction_score
            score_component = 20 if current_satisfaction <= 2 else 10 if current_satisfaction == 3 else 0
        risk_score = sla_component + escalation_component + complaint_component + score_component
        row = _ticket_to_row(ticket)
        row.update(
            {
                "risk_score": risk_score,
                "sla_remaining_hours": remaining_hours,
                "has_escalation": bool(ticket.escalations),
                "complaint_weight": complaint_component,
                "current_satisfaction": current_satisfaction,
            }
        )
        rows.append(mask_sensitive_fields(row, context))
    total_count = len(rows)
    rows.sort(key=lambda row: (-row["risk_score"], row["sla_remaining_hours"], row["ticket_id"]))
    rows = rows[: _limit_value(filters)]
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_feedback(context, filters: QueryFilters, low_score_only: bool = False):
    filters = canonicalize_query_filters(filters)
    if filters.query_kind == "low_feedback_risk":
        return query_low_feedback_risk_analysis(context, filters)
    if filters.query_kind == "feedback_type_stats":
        return query_feedback_stats(context, filters)
    query = (
        Feedback.query.join(Ticket, Feedback.ticket_id == Ticket.ticket_id)
        .join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .join(Passenger, Feedback.passenger_id == Passenger.passenger_id)
        .options(
            joinedload(Feedback.ticket).joinedload(Ticket.department),
            joinedload(Feedback.ticket).joinedload(Ticket.complaint),
            joinedload(Feedback.ticket).joinedload(Ticket.complaint).joinedload(Complaint.order).joinedload(RideOrder.driver),
            joinedload(Feedback.ticket).joinedload(Ticket.current_owner),
            joinedload(Feedback.passenger),
        )
    )
    query = apply_ticket_scope(query, context)
    if filters.ticket_id:
        query = query.filter(Feedback.ticket_id == filters.ticket_id)
    if filters.complaint_id:
        query = query.filter(Ticket.complaint_id == filters.complaint_id)
    if filters.order_id:
        query = query.filter(Complaint.order_id == filters.order_id)
    if filters.driver_id:
        query = query.join(RideOrder, Complaint.order_id == RideOrder.order_id).filter(RideOrder.driver_id == filters.driver_id)
    if filters.complaint_type:
        query = query.filter(ComplaintType.type_name == filters.complaint_type)
    if filters.department_name:
        query = query.filter(Department.department_name == filters.department_name)
    if low_score_only or filters.score_below is not None:
        threshold = filters.score_below or 3
        query = query.filter(Feedback.satisfaction_score < threshold)
    start_dt, end_dt = _parse_date_bounds(filters)
    if start_dt:
        query = query.filter(Feedback.feedback_time >= start_dt)
    if end_dt:
        query = query.filter(Feedback.feedback_time < end_dt)

    total_count = query.order_by(None).count()
    query = query.order_by(Feedback.feedback_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_feedback")]
    feedback_rows = query.all()
    rows = []
    for feedback in feedback_rows:
        ticket = feedback.ticket
        complaint = ticket.complaint if ticket else None
        row = {
            "feedback_id": feedback.feedback_id,
            "ticket_id": feedback.ticket_id,
            "passenger_name": feedback.passenger.passenger_name if feedback.passenger else "-",
            "satisfaction_score": feedback.satisfaction_score,
            "feedback_content": feedback.feedback_content,
            "feedback_time": feedback.feedback_time.strftime("%Y-%m-%d %H:%M:%S"),
            "complaint_type": complaint.complaint_type.type_name if complaint and complaint.complaint_type else "-",
            "complaint_content": complaint.complaint_content if complaint else "-",
            "order_id": complaint.order_id if complaint else "-",
            "driver_name": complaint.order.driver.driver_name if complaint and complaint.order and complaint.order.driver else "-",
            "department_name": ticket.department.department_name if ticket and ticket.department else "-",
            "ticket_status": ticket.ticket_status if ticket else "-",
            "current_owner": ticket.current_owner.employee_name if ticket and ticket.current_owner else "未分派",
            "is_overdue": False,
        }
        rows.append(mask_sensitive_fields(row, context))
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def query_risk_tickets(context, filters: QueryFilters):
    filters = canonicalize_query_filters(filters)
    if filters.query_kind == "ticket_risk_scoring":
        return query_ticket_risk_scoring(context, filters)
    query = apply_ticket_scope(_ticket_base_query(), context)
    query = _apply_ticket_filters(query, filters)
    query = query.filter(
        (Ticket.priority_level == "P1")
        | (Ticket.ticket_status == "已升级")
        | (Ticket.ticket_status == "待反馈")
        | ((Ticket.ticket_status != "已关闭") & (Ticket.sla_deadline < datetime.now()))
        | (ComplaintType.type_name == "安全事件")
    )
    total_count = query.order_by(None).count()
    query = query.order_by(Ticket.sla_deadline.asc(), Ticket.create_time.desc()).limit(_limit_value(filters))
    sql_debug = [_compile_query_sql(query, "query_risk_tickets")]
    tickets = query.all()
    rows = [mask_sensitive_fields(_ticket_to_row(ticket), context) for ticket in tickets]
    return {"rows": rows, "data_count": total_count, "risk_flags": _derive_risk_flags(rows), "sql_debug": sql_debug}


def suggest_ticket_action(context, ticket_id: str):
    detail = get_ticket_detail(context, ticket_id)
    if detail["data_count"] == 0:
        return detail

    row = detail["rows"][0]
    suggestions = []
    if row["ticket_status"] == "待分派":
        suggestions.append("建议先进入工单详情页完成分派，明确责任人。")
    if row["is_overdue"]:
        suggestions.append("工单已超时，建议优先补充处理日志并评估是否需要升级。")
    if row["priority_level"] == "P1":
        suggestions.append("P1 工单建议立即复核处理进度，并同步主管或安全负责人。")
    if row.get("action_log_count", 0) == 0:
        suggestions.append("当前缺少处理日志，建议先补充核查动作和处理依据。")
    if row["ticket_status"] == "待反馈":
        suggestions.append("工单已进入待反馈阶段，可在后台提交乘客反馈。")
    if not suggestions:
        suggestions.append("当前工单状态正常，建议继续沿既有流程跟进并关注 SLA。")

    suggestion_row = {
        "ticket_id": row["ticket_id"],
        "ticket_status": row["ticket_status"],
        "priority_level": row["priority_level"],
        "department_name": row["department_name"],
        "current_owner": row["current_owner"],
        "is_overdue": row["is_overdue"],
        "suggested_actions": "；".join(suggestions),
        "recommended_page": f"/tickets/{row['ticket_id']}",
    }
    suggestion_row = mask_sensitive_fields(suggestion_row, context)
    return {
        "rows": [suggestion_row],
        "data_count": 1,
        "risk_flags": _derive_risk_flags([row]),
        "sql_debug": detail.get("sql_debug", []),
    }


def _get_visible_ticket(context, ticket_id: str):
    ticket_id = resolve_ticket_identifier(ticket_id)
    if not ticket_id:
        return None
    return apply_ticket_scope(_ticket_base_query(), context).filter(Ticket.ticket_id == ticket_id.upper()).first()


def _get_actor(context):
    return Employee.query.filter_by(employee_id=context.employee_id).first()


def _get_visible_complaint(context, complaint_id: str):
    complaint_id = resolve_complaint_identifier(complaint_id)
    if not complaint_id:
        return None
    query = (
        Complaint.query.join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .filter(Complaint.complaint_id == complaint_id)
    )
    return apply_ticket_scope(query, context).first()


def _resolve_receiver(payload):
    if getattr(payload, "receiver_id", None):
        receiver = Employee.query.filter_by(
            employee_id=resolve_employee_identifier(payload.receiver_id),
            employee_status="在职",
        ).first()
        if receiver:
            return receiver
    if getattr(payload, "receiver_name", None):
        exact = Employee.query.filter_by(employee_name=payload.receiver_name, employee_status="在职").first()
        if exact:
            return exact
        fuzzy = (
            Employee.query.filter(Employee.employee_status == "在职", Employee.employee_name.contains(payload.receiver_name))
            .order_by(Employee.employee_id.asc())
            .all()
        )
        if len(fuzzy) == 1:
            return fuzzy[0]
        if payload.receiver_name:
            surname = payload.receiver_name[0]
            surname_matches = (
                Employee.query.filter(Employee.employee_status == "在职", Employee.employee_name.contains(surname))
                .order_by(Employee.employee_id.asc())
                .all()
            )
            if len(surname_matches) == 1:
                return surname_matches[0]
    return None


def create_complaint_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    if not actor or not can_create_complaint(actor):
        raise PermissionError("当前角色无权创建投诉。")
    if not payload.order_id:
        raise ValueError("请补充订单编号，例如 ORD001。")
    if not payload.complaint_type:
        raise ValueError("请补充投诉类型。")
    payload.urgency_level = payload.urgency_level or "U2"
    if not payload.complaint_content:
        payload.complaint_content = f"智能助手录入投诉：{payload.complaint_type}"

    order = RideOrder.query.filter_by(order_id=payload.order_id).first()
    if not order:
        raise ValueError("未找到对应订单。")
    complaint_type = ComplaintType.query.filter_by(type_name=payload.complaint_type).first()
    if not complaint_type:
        raise ValueError("未找到对应投诉类型。")

    complaint, ticket = create_complaint_and_ticket(
        order=order,
        complaint_type=complaint_type,
        complaint_content=payload.complaint_content,
        urgency_level=payload.urgency_level,
    )
    db.session.commit()

    row = {
        "action": "create_complaint",
        "complaint_id": complaint.complaint_id,
        "ticket_id": ticket.ticket_id,
        "order_id": order.order_id,
        "complaint_type": complaint_type.type_name,
        "urgency_level": complaint.urgency_level,
        "ticket_status": ticket.ticket_status,
        "department_name": ticket.department.department_name if ticket.department else "-",
        "priority_level": ticket.priority_level,
        "sla_deadline": ticket.sla_deadline.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def update_order_status_action(context, payload):
    actor = _get_actor(context)
    if not actor or actor.role not in {"admin", "manager", "customer_service"}:
        raise PermissionError("当前角色无权修改订单状态。")
    if not payload.order_id:
        raise ValueError("请补充订单编号。")
    if not payload.order_status:
        raise ValueError("请补充订单状态。")

    order = RideOrder.query.filter_by(order_id=payload.order_id).first()
    if not order:
        raise ValueError("未找到对应订单。")

    previous_status = order.order_status
    order.order_status = payload.order_status
    if payload.order_status == "已完成" and not order.finish_time:
        order.finish_time = now()
    db.session.commit()

    row = {
        "action": "update_order_status",
        "order_id": order.order_id,
        "previous_status": previous_status,
        "order_status": order.order_status,
        "finish_time": order.finish_time.strftime("%Y-%m-%d %H:%M:%S") if order.finish_time else "-",
    }
    return {"rows": [row], "data_count": 1, "risk_flags": [], "sql_debug": []}


def delete_order_action(context, payload):
    actor = _get_actor(context)
    if not actor or actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权删除订单。")
    if not payload.order_id:
        raise ValueError("请补充订单编号。")

    order = RideOrder.query.filter_by(order_id=payload.order_id).first()
    if not order:
        raise ValueError("未找到对应订单。")
    if order.complaints:
        raise ValueError("该订单已关联投诉或工单，不能直接删除。")

    row = {
        "action": "delete_order",
        "order_id": order.order_id,
        "order_status": order.order_status,
    }
    db.session.delete(order)
    db.session.commit()
    return {"rows": [row], "data_count": 1, "risk_flags": [], "sql_debug": []}


def update_complaint_urgency_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    if not actor or actor.role not in {"admin", "manager", "customer_service"}:
        raise PermissionError("当前角色无权修改投诉紧急程度。")
    if not payload.complaint_id:
        raise ValueError("请补充投诉编号。")
    complaint = _get_visible_complaint(context, payload.complaint_id)
    if not complaint:
        raise ValueError("未找到当前权限范围内的投诉。")
    urgency_level = payload.urgency_level or "U1"
    previous_level = complaint.urgency_level
    complaint.urgency_level = urgency_level
    db.session.commit()
    row = {
        "action": "update_complaint_urgency",
        "complaint_id": complaint.complaint_id,
        "previous_urgency_level": previous_level,
        "urgency_level": complaint.urgency_level,
        "order_id": complaint.order_id,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": [], "sql_debug": []}


def delete_complaint_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    if not actor or actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权删除投诉。")
    if not payload.complaint_id:
        raise ValueError("请补充投诉编号。")
    complaint = Complaint.query.filter_by(complaint_id=payload.complaint_id).first()
    if not complaint:
        raise ValueError("未找到对应投诉。")
    ticket = complaint.ticket
    if ticket and (ticket.assignments or ticket.action_logs or ticket.escalations or ticket.feedback):
        raise ValueError("该投诉已经进入工单处理流程，不能直接删除。")
    row = {
        "action": "delete_complaint",
        "complaint_id": complaint.complaint_id,
        "order_id": complaint.order_id,
        "complaint_type": complaint.complaint_type.type_name if complaint.complaint_type else "-",
    }
    if ticket:
        db.session.delete(ticket)
    db.session.delete(complaint)
    db.session.commit()
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": [], "sql_debug": []}


def delete_ticket_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权删除工单。")
    row = {
        "action": "delete_ticket",
        "ticket_id": ticket.ticket_id,
        "ticket_status": ticket.ticket_status,
        "complaint_id": ticket.complaint_id,
        "order_id": ticket.complaint.order_id if ticket.complaint else "-",
    }
    # 级联清理依赖记录，再删除工单；投诉保留，可重新生成工单
    for log in list(ticket.action_logs):
        db.session.delete(log)
    for assignment in list(ticket.assignments):
        db.session.delete(assignment)
    for escalation in list(ticket.escalations):
        db.session.delete(escalation)
    if ticket.feedback:
        db.session.delete(ticket.feedback)
    db.session.delete(ticket)
    db.session.commit()
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": [], "sql_debug": []}


def create_ticket_for_complaint_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    if not actor or actor.role not in {"admin", "manager", "customer_service"}:
        raise PermissionError("当前角色无权基于投诉创建工单。")
    if not payload.complaint_id:
        raise ValueError("请补充投诉编号。")

    complaint = Complaint.query.filter_by(complaint_id=payload.complaint_id).first()
    if not complaint:
        raise ValueError("未找到对应投诉。")
    if complaint.ticket:
        ticket = complaint.ticket
        row = {
            "action": "create_ticket_for_complaint",
            "complaint_id": complaint.complaint_id,
            "ticket_id": ticket.ticket_id,
            "ticket_status": ticket.ticket_status,
            "message": "该投诉已存在关联工单，返回现有工单。",
        }
        return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row]), "sql_debug": []}

    ticket = Ticket(
        ticket_id=generate_id("TCK"),
        complaint_id=complaint.complaint_id,
        priority_level=complaint.complaint_type.default_priority_level,
        ticket_status="待分派",
        department_id=complaint.complaint_type.default_department_id,
        current_owner_id=None,
        create_time=now(),
        sla_deadline=now() + timedelta(hours=complaint.complaint_type.default_sla_hours),
    )
    db.session.add(ticket)
    db.session.commit()
    row = {
        "action": "create_ticket_for_complaint",
        "complaint_id": complaint.complaint_id,
        "ticket_id": ticket.ticket_id,
        "ticket_status": ticket.ticket_status,
        "department_name": ticket.department.department_name if ticket.department else "-",
        "priority_level": ticket.priority_level,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row]), "sql_debug": []}


def assign_ticket_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_assign_ticket(actor, ticket):
        raise PermissionError("当前角色无权分派工单。")
    receiver = _resolve_receiver(payload)
    if not receiver:
        raise ValueError("请补充有效的接收员工。")

    record = assign_ticket(ticket, actor, receiver, payload.assignment_note or "")
    db.session.commit()

    row = {
        "action": "assign_ticket",
        "ticket_id": ticket.ticket_id,
        "assignment_id": record.assignment_id,
        "department_name": ticket.department.department_name if ticket.department else "-",
        "current_owner": receiver.employee_name,
        "ticket_status": ticket.ticket_status,
        "assignment_note": record.assignment_note or "-",
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def add_action_log_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_add_log(actor, ticket):
        raise PermissionError("当前角色无权新增处理日志。")
    action_type = payload.action_type or "其他"
    if action_type not in ACTION_TYPES:
        action_type = "其他"
    if not payload.action_content:
        payload.action_content = f"通过智能助手补充处理记录：{action_type}"

    log = add_action_log(ticket, actor, action_type, payload.action_content)
    db.session.commit()

    row = {
        "action": "add_action_log",
        "ticket_id": ticket.ticket_id,
        "log_id": log.log_id,
        "action_type": log.action_type,
        "action_content": log.action_content,
        "ticket_status": ticket.ticket_status,
        "current_owner": ticket.current_owner.employee_name if ticket.current_owner else "未分派",
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def escalate_ticket_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_escalate_ticket(actor, ticket):
        raise PermissionError("当前角色无权升级该工单。")
    level_alias = {"L1": "一线处理", "L2": "主管复核", "L3": "跨部门协调", "L4": "平台管理层"}
    if payload.to_level in level_alias:
        payload.to_level = level_alias[payload.to_level]
    if payload.from_level in level_alias:
        payload.from_level = level_alias[payload.from_level]
    if not payload.to_level or payload.to_level not in TO_LEVEL_OPTIONS:
        raise ValueError("请补充有效的目标处理层级。")
    if not payload.escalation_reason:
        payload.escalation_reason = "通过智能助手发起升级，请相关层级复核。"

    from_level = payload.from_level or (ticket.department.department_name if ticket.department else "一线处理")
    record = escalate_ticket(ticket, actor, from_level, payload.to_level, payload.escalation_reason)
    db.session.commit()

    row = {
        "action": "escalate_ticket",
        "ticket_id": ticket.ticket_id,
        "escalation_id": record.escalation_id,
        "from_level": record.from_level,
        "to_level": record.to_level,
        "ticket_status": ticket.ticket_status,
        "escalation_reason": record.escalation_reason,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def update_ticket_priority_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权修改工单优先级。")
    if payload.priority_level not in {"P1", "P2", "P3", "P4"}:
        raise ValueError("请提供有效的优先级，例如 P1。")
    previous_level = ticket.priority_level
    ticket.priority_level = payload.priority_level
    db.session.commit()
    row = {
        "action": "update_ticket_priority",
        "ticket_id": ticket.ticket_id,
        "previous_priority_level": previous_level,
        "priority_level": ticket.priority_level,
        "ticket_status": ticket.ticket_status,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row]), "sql_debug": []}


def close_ticket_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权直接关闭工单。")
    if count_action_logs(ticket) < 1:
        raise ValueError("工单关闭前必须至少存在一条处理日志。")

    close_time = now()
    ticket.ticket_status = "已关闭"
    ticket.close_time = close_time
    ticket.complaint.complaint_status = "已关闭"
    db.session.add(
        ActionLog(
            log_id=generate_id("LOG"),
            ticket_id=ticket.ticket_id,
            employee_id=actor.employee_id,
            action_type="关闭工单",
            action_content=payload.close_reason or "通过智能助手直接关闭工单。",
            action_time=close_time,
        )
    )
    db.session.commit()
    row = {
        "action": "close_ticket",
        "ticket_id": ticket.ticket_id,
        "ticket_status": ticket.ticket_status,
        "close_time": ticket.close_time.strftime("%Y-%m-%d %H:%M:%S") if ticket.close_time else "-",
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row]), "sql_debug": []}


def set_pending_feedback_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_set_pending_feedback(actor, ticket):
        raise PermissionError("当前角色无权将工单设为待反馈。")

    log = set_ticket_pending_feedback(ticket, actor)
    db.session.commit()
    row = {
        "action": "set_pending_feedback",
        "ticket_id": ticket.ticket_id,
        "log_id": log.log_id,
        "ticket_status": ticket.ticket_status,
        "action_type": log.action_type,
        "action_content": log.action_content,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def submit_feedback_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_submit_feedback(actor, ticket):
        raise PermissionError("当前角色无权提交反馈。")
    if payload.satisfaction_score is None:
        raise ValueError("请补充满意度评分。")
    if not payload.feedback_content:
        payload.feedback_content = "通过智能助手录入反馈。"

    feedback = submit_feedback(ticket, payload.satisfaction_score, payload.feedback_content)
    db.session.commit()
    row = {
        "action": "submit_feedback",
        "ticket_id": ticket.ticket_id,
        "feedback_id": feedback.feedback_id,
        "satisfaction_score": feedback.satisfaction_score,
        "feedback_content": feedback.feedback_content,
        "ticket_status": ticket.ticket_status,
        "close_time": ticket.close_time.strftime("%Y-%m-%d %H:%M:%S") if ticket.close_time else "-",
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def reopen_ticket_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if not can_reopen_ticket(actor, ticket):
        raise PermissionError("当前角色无权重开工单。")

    log = reopen_ticket(ticket, actor)
    db.session.commit()
    row = {
        "action": "reopen_ticket",
        "ticket_id": ticket.ticket_id,
        "log_id": log.log_id,
        "ticket_status": ticket.ticket_status,
        "action_type": log.action_type,
        "action_content": log.action_content,
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}


def revoke_assignment_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    ticket = _get_visible_ticket(context, payload.ticket_id)
    if not actor or not ticket:
        raise ValueError("未找到当前权限范围内的工单。")
    if actor.role not in {"admin", "manager"}:
        raise PermissionError("当前角色无权撤销分派记录。")
    latest = (
        AssignmentRecord.query.filter_by(ticket_id=ticket.ticket_id)
        .order_by(AssignmentRecord.assign_time.desc(), AssignmentRecord.assignment_id.desc())
        .first()
    )
    if not latest:
        raise ValueError("该工单暂无可撤销的分派记录。")

    deleted_assignment_id = latest.assignment_id
    deleted_receiver_name = latest.receiver.employee_name if latest.receiver else "-"
    db.session.delete(latest)
    db.session.flush()

    previous = (
        AssignmentRecord.query.filter_by(ticket_id=ticket.ticket_id)
        .order_by(AssignmentRecord.assign_time.desc(), AssignmentRecord.assignment_id.desc())
        .first()
    )
    if previous:
        ticket.current_owner_id = previous.receiver_id
        ticket.department_id = previous.department_id
        ticket.ticket_status = "处理中"
    else:
        ticket.current_owner_id = None
        ticket.department_id = ticket.complaint.complaint_type.default_department_id
        ticket.ticket_status = "待分派"
    db.session.commit()

    row = {
        "action": "revoke_assignment",
        "ticket_id": ticket.ticket_id,
        "revoked_assignment_id": deleted_assignment_id,
        "revoked_receiver_name": deleted_receiver_name,
        "ticket_status": ticket.ticket_status,
        "current_owner": ticket.current_owner.employee_name if ticket.current_owner else "未分派",
    }
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row]), "sql_debug": []}


def delete_action_log_action(context, payload):
    payload = canonicalize_action_payload(payload)
    actor = _get_actor(context)
    if not actor:
        raise PermissionError("当前角色无权删除处理日志。")
    if not payload.log_id and payload.ticket_id:
        # 未指定日志编号时，按工单取最新一条处理日志（支持“删除某工单最新/最近一条日志”）
        ticket = _get_visible_ticket(context, payload.ticket_id)
        if ticket and ticket.action_logs:
            payload.log_id = sorted(ticket.action_logs, key=lambda item: item.action_time)[-1].log_id
    if not payload.log_id:
        raise ValueError("请补充要删除的日志编号，或指定工单以删除其最新一条处理日志。")

    log = (
        ActionLog.query.join(Ticket, ActionLog.ticket_id == Ticket.ticket_id)
        .join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .join(Department, Ticket.department_id == Department.department_id)
        .filter(ActionLog.log_id == payload.log_id)
    )
    log = apply_ticket_scope(log, context).first()
    if not log:
        raise ValueError("未找到当前权限范围内的处理日志。")
    if log.ticket.ticket_status == "已关闭":
        raise ValueError("已关闭工单的处理日志不允许通过智能助手删除。")
    if count_action_logs(log.ticket) <= 1:
        raise ValueError("至少需要保留一条处理日志。")

    row = {
        "action": "delete_action_log",
        "ticket_id": log.ticket_id,
        "log_id": log.log_id,
        "action_type": log.action_type,
        "action_content": log.action_content,
        "delete_reason": payload.delete_reason or "智能助手删除处理日志",
    }
    db.session.delete(log)
    db.session.commit()
    return {"rows": [mask_sensitive_fields(row, context)], "data_count": 1, "risk_flags": _derive_risk_flags([row])}
