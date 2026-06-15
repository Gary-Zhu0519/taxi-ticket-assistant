from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_

from models import ComplaintType, Department, Employee, ROLE_LABELS, Ticket

FINANCE_COMPLAINT_TYPES = {"费用争议", "支付退款", "多收费"}
SAFETY_COMPLAINT_TYPES = {"安全事件"}
OPERATION_COMPLAINT_TYPES = {"司机服务", "取消争议", "车辆信息不符"}
ROLE_DASHBOARD_DEPARTMENTS = {
    "customer_service": {"客服部"},
    "finance": {"财务售后部"},
    "safety": {"安全部"},
    "operation": {"运营部"},
}


@dataclass(slots=True)
class UserContext:
    employee_id: str
    employee_name: str
    role: str
    role_label: str
    department_id: str
    department_name: str
    scope_description: str


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "-"
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def _shorten_text(text: str | None, limit: int = 24) -> str:
    if not text:
        return "-"
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def build_user_context(current_user: Employee) -> UserContext:
    department_name = current_user.department.department_name if current_user.department else "未分配部门"
    return UserContext(
        employee_id=current_user.employee_id,
        employee_name=current_user.employee_name,
        role=current_user.role,
        role_label=ROLE_LABELS.get(current_user.role, current_user.role),
        department_id=current_user.department_id,
        department_name=department_name,
        scope_description=get_allowed_scope(
            UserContext(
                employee_id=current_user.employee_id,
                employee_name=current_user.employee_name,
                role=current_user.role,
                role_label=ROLE_LABELS.get(current_user.role, current_user.role),
                department_id=current_user.department_id,
                department_name=department_name,
                scope_description="",
            )
        ),
    )


def get_allowed_scope(context: UserContext) -> str:
    return {
        "admin": "可查询全量订单、投诉、工单、反馈和统计数据。",
        "manager": "可查询全量工单、反馈与统计数据，适合主管汇总与复核。",
        "customer_service": "仅可查询客服部工单、待分派工单和普通投诉受理信息。",
        "finance": "仅可查询财务售后部工单，以及费用争议、支付退款、多收费相关工单。",
        "safety": "仅可查询安全部工单、安全事件工单和 P1 高优先级工单。",
        "operation": "仅可查询运营部工单，以及司机服务、取消争议、车辆信息不符相关工单。",
        "employee": "仅可查询当前负责人为本人且未关闭的工单，不可查看部门整体统计。",
    }.get(context.role, "仅可查询当前角色权限范围内的数据。")


def get_allowed_dashboard_departments(context: UserContext) -> set[str]:
    if context.role in {"admin", "manager"}:
        return set()
    return ROLE_DASHBOARD_DEPARTMENTS.get(context.role, set())


def check_permission(context: UserContext, intent: str, filters) -> dict:
    if intent == "permission_sensitive":
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "当前角色无权查询该范围数据。",
        }

    if intent == "dashboard_summary":
        if context.role == "employee":
            return {
                "allowed": False,
                "error_code": "PERMISSION_DENIED",
                "message": "当前角色无权查询部门统计数据。",
            }
        allowed_departments = get_allowed_dashboard_departments(context)
        if allowed_departments:
            if filters.department_name and filters.department_name not in allowed_departments:
                return {
                    "allowed": False,
                    "error_code": "PERMISSION_DENIED",
                    "message": "当前角色无权查询该部门统计数据。",
                }

    if intent == "feedback_query" and context.role == "employee":
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "普通员工无权直接查询反馈记录。",
        }

    if context.role == "employee":
        if filters.employee_name and filters.employee_name not in {context.employee_name, "我", "我的"}:
            return {
                "allowed": False,
                "error_code": "PERMISSION_DENIED",
                "message": "普通员工不能查询其他员工的工单。",
            }
        if filters.department_name and filters.department_name != context.department_name:
            return {
                "allowed": False,
                "error_code": "PERMISSION_DENIED",
                "message": "普通员工不能按部门查询工单。",
            }

    if context.role == "finance" and filters.complaint_type in SAFETY_COMPLAINT_TYPES:
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "财务角色无权查询安全事件详情。",
        }

    if context.role == "operation" and filters.complaint_type in FINANCE_COMPLAINT_TYPES:
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "运营角色无权查询财务处理细节。",
        }

    return {"allowed": True, "error_code": None, "message": ""}


def check_action_permission(context: UserContext, action: str, payload) -> dict:
    if action in {"unsupported", "permission_sensitive"}:
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "当前角色无权执行该智能写操作。",
        }

    if action == "create_complaint":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "update_order_status":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "delete_order":
        allowed = context.role in {"admin", "manager"}
    elif action == "update_complaint_urgency":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "delete_complaint":
        allowed = context.role in {"admin", "manager"}
    elif action == "delete_ticket":
        allowed = context.role in {"admin", "manager"}
    elif action == "create_ticket_for_complaint":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "assign_ticket":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "add_action_log":
        allowed = context.role in {"admin", "manager", "finance", "safety", "operation", "employee"}
    elif action == "escalate_ticket":
        allowed = context.role in {"admin", "manager", "finance", "safety", "operation", "employee"}
    elif action == "update_ticket_priority":
        allowed = context.role in {"admin", "manager"}
    elif action == "close_ticket":
        allowed = context.role in {"admin", "manager"}
    elif action == "set_pending_feedback":
        allowed = context.role in {"admin", "manager", "finance", "safety", "operation", "employee"}
    elif action == "submit_feedback":
        allowed = context.role in {"admin", "manager", "customer_service"}
    elif action == "reopen_ticket":
        allowed = context.role in {"admin", "manager", "finance", "safety", "operation", "employee"}
    elif action == "delete_action_log":
        allowed = context.role in {"admin", "manager"}
    elif action == "revoke_assignment":
        allowed = context.role in {"admin", "manager"}
    else:
        allowed = False

    if not allowed:
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "当前角色无权执行该智能写操作。",
        }

    if context.role == "employee" and action in {
        "assign_ticket",
        "submit_feedback",
        "delete_action_log",
        "create_complaint",
        "update_order_status",
        "delete_order",
        "update_complaint_urgency",
        "delete_complaint",
        "create_ticket_for_complaint",
        "update_ticket_priority",
        "close_ticket",
        "revoke_assignment",
    }:
        return {
            "allowed": False,
            "error_code": "PERMISSION_DENIED",
            "message": "普通员工无权执行该操作。",
        }

    return {"allowed": True, "error_code": None, "message": ""}


def apply_ticket_scope(query, context: UserContext):
    if context.role in {"admin", "manager"}:
        return query
    if context.role == "customer_service":
        return query.filter(
            or_(
                Department.department_name == "客服部",
                Ticket.ticket_status == "待分派",
            )
        )
    if context.role == "finance":
        return query.filter(
            or_(
                Department.department_name == "财务售后部",
                ComplaintType.type_name.in_(tuple(FINANCE_COMPLAINT_TYPES)),
            )
        )
    if context.role == "safety":
        return query.filter(
            or_(
                Department.department_name == "安全部",
                ComplaintType.type_name.in_(tuple(SAFETY_COMPLAINT_TYPES)),
                Ticket.priority_level == "P1",
            )
        )
    if context.role == "operation":
        return query.filter(
            or_(
                Department.department_name == "运营部",
                ComplaintType.type_name.in_(tuple(OPERATION_COMPLAINT_TYPES)),
            )
        )
    if context.role == "employee":
        return query.filter(
            Ticket.current_owner_id == context.employee_id,
            Ticket.ticket_status != "已关闭",
        )
    return query.filter(Ticket.ticket_id == "__forbidden__")


def mask_sensitive_fields(row: dict, context: UserContext) -> dict:
    masked = dict(row)

    for key in list(masked.keys()):
        if "password" in key.lower():
            masked.pop(key, None)

    if context.role == "employee":
        for key in list(masked.keys()):
            if "phone" in key.lower():
                masked[key] = _mask_phone(masked.get(key))

    complaint_type = masked.get("complaint_type") or masked.get("type_name")
    if complaint_type == "安全事件" and context.role not in {"admin", "manager", "safety"}:
        if masked.get("complaint_content"):
            masked["complaint_content"] = f"{_shorten_text(masked['complaint_content'], 16)}（敏感内容已脱敏）"
        if masked.get("feedback_content"):
            masked["feedback_content"] = "安全事件反馈详情已按权限脱敏。"
        if masked.get("action_summary"):
            masked["action_summary"] = "安全事件处理摘要已按权限脱敏。"

    if context.role == "operation" and complaint_type in FINANCE_COMPLAINT_TYPES:
        if masked.get("complaint_content"):
            masked["complaint_content"] = "财务处理细节已按权限脱敏。"
        if masked.get("feedback_content"):
            masked["feedback_content"] = "财务反馈细节已按权限脱敏。"

    return masked
