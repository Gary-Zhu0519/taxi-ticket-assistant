from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_, text
from sqlalchemy.orm import joinedload

from auth import login_required
from database import db
from models import (
    PRIORITY_CHOICES,
    TICKET_STATUS_CHOICES,
    URGENCY_CHOICES,
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
    apply_complaint_scope,
    apply_ticket_scope,
    assign_ticket,
    can_add_log,
    can_assign_ticket,
    can_create_complaint,
    can_escalate_ticket,
    can_reopen_ticket,
    can_set_pending_feedback,
    can_submit_feedback,
    create_complaint_and_ticket,
    escalate_ticket,
    get_accessible_departments,
    get_role_scope_name,
    get_scope_stats,
    reopen_ticket,
    require_ticket_access,
    set_ticket_pending_feedback,
    submit_feedback,
)

main_bp = Blueprint("main", __name__)

SCHEMA_TABLES = [
    {
        "name": "Passenger",
        "title": "乘客表",
        "pk": "passenger_id",
        "fks": "-",
        "description": "保存投诉来源乘客基础信息。",
    },
    {
        "name": "Driver",
        "title": "司机表",
        "pk": "driver_id",
        "fks": "-",
        "description": "保存订单关联司机信息。",
    },
    {
        "name": "Ride_Order",
        "title": "订单表",
        "pk": "order_id",
        "fks": "passenger_id -> Passenger, driver_id -> Driver",
        "description": "简化订单背景，不实现完整打车业务。",
    },
    {
        "name": "Complaint_Type",
        "title": "投诉类型表",
        "pk": "complaint_type_id",
        "fks": "default_department_id -> Department",
        "description": "分类字典与默认规则表，决定默认部门、优先级和 SLA。",
    },
    {
        "name": "Complaint",
        "title": "投诉表",
        "pk": "complaint_id",
        "fks": "order_id -> Ride_Order, passenger_id -> Passenger, complaint_type_id -> Complaint_Type",
        "description": "记录乘客对订单提出的原始问题。",
    },
    {
        "name": "Department",
        "title": "部门表",
        "pk": "department_id",
        "fks": "manager_id -> Employee",
        "description": "定义投诉工单责任部门和主管。",
    },
    {
        "name": "Employee",
        "title": "员工表",
        "pk": "employee_id",
        "fks": "department_id -> Department",
        "description": "定义系统用户、角色和登录信息。",
    },
    {
        "name": "Ticket",
        "title": "工单表",
        "pk": "ticket_id",
        "fks": "complaint_id -> Complaint, department_id -> Department, current_owner_id -> Employee",
        "description": "记录后台对投诉的处理任务，是系统闭环主表。",
    },
    {
        "name": "Assignment_Record",
        "title": "分派记录表",
        "pk": "assignment_id",
        "fks": "ticket_id -> Ticket, assigner_id -> Employee, receiver_id -> Employee, department_id -> Department",
        "description": "保存工单从谁分给谁、分到哪个部门的过程。",
    },
    {
        "name": "Escalation_Record",
        "title": "升级记录表",
        "pk": "escalation_id",
        "fks": "ticket_id -> Ticket, escalated_by -> Employee",
        "description": "保存高优先级、复杂或超时工单的升级历史。",
    },
    {
        "name": "Action_Log",
        "title": "处理日志表",
        "pk": "log_id",
        "fks": "ticket_id -> Ticket, employee_id -> Employee",
        "description": "保存联系乘客、联系司机、核查订单等处理动作。",
    },
    {
        "name": "Feedback",
        "title": "反馈表",
        "pk": "feedback_id",
        "fks": "ticket_id -> Ticket, passenger_id -> Passenger",
        "description": "记录乘客满意度与反馈内容，每个工单最多一条。",
    },
]

SCHEMA_VIEWS = [
    "v_customer_service_ticket",
    "v_finance_complaint_ticket",
    "v_safety_ticket",
    "v_operation_ticket",
    "v_manager_ticket_summary",
    "v_employee_pending_ticket",
    "v_feedback_result",
]

SCHEMA_INDEXES = [
    "idx_order_passenger",
    "idx_order_driver",
    "idx_complaint_order",
    "idx_complaint_type",
    "idx_ticket_complaint",
    "idx_ticket_status",
    "idx_ticket_owner",
    "idx_ticket_department",
    "idx_assignment_ticket",
    "idx_escalation_ticket",
    "idx_action_ticket",
    "idx_feedback_ticket",
]

WORKFLOW_STEPS = [
    "订单作为投诉来源，提供投诉发生的业务背景。",
    "后台人员基于订单录入投诉，投诉状态初始为“已受理”。",
    "系统依据投诉类型默认规则自动生成工单，并写入责任部门、优先级与 SLA。",
    "管理员、主管或客服将工单分派给具体员工，工单进入处理中。",
    "员工写入处理日志，补充联系乘客、联系司机、核查订单等动作。",
    "复杂、超时或高优先级工单可以创建升级记录，状态切换为“已升级”。",
    "处理完成后转入“待反馈”，后台模拟录入乘客满意度。",
    "满意度 >= 3 自动关闭工单；满意度 < 3 自动重开工单，形成闭环。",
]

CORE_RELATIONSHIPS = [
    "Passenger 1 对多 Ride_Order",
    "Driver 1 对多 Ride_Order",
    "Ride_Order 1 对多 Complaint",
    "Complaint_Type 1 对多 Complaint",
    "Complaint 1 对 1 Ticket",
    "Department 1 对多 Employee",
    "Department 1 对多 Ticket",
    "Ticket 1 对多 Assignment_Record",
    "Ticket 1 对多 Escalation_Record",
    "Ticket 1 对多 Action_Log",
    "Ticket 1 对 0 或 1 Feedback",
]

SCHEMA_VIEW_DETAILS = [
    ("v_customer_service_ticket", "客服工单视图，聚合投诉、订单、乘客、优先级、状态和 SLA。"),
    ("v_finance_complaint_ticket", "财务售后视图，只展示费用争议及财务售后相关工单。"),
    ("v_safety_ticket", "安全视图，只展示安全事件和 P1 高优先级工单。"),
    ("v_operation_ticket", "运营视图，只展示司机服务、取消争议等运营相关工单。"),
    ("v_manager_ticket_summary", "主管统计视图，按部门汇总总数、处理中、已关闭和超时数量。"),
    ("v_employee_pending_ticket", "员工待办视图，展示当前负责人未关闭工单。"),
    ("v_feedback_result", "反馈结果视图，汇总满意度、反馈内容、投诉类型和订单信息。"),
]

SCHEMA_INDEX_DETAILS = [
    ("idx_order_passenger", "优化按乘客查询订单。"),
    ("idx_order_driver", "优化按司机查询订单。"),
    ("idx_complaint_order", "优化按订单查询投诉。"),
    ("idx_complaint_type", "优化按投诉类型统计投诉。"),
    ("idx_ticket_complaint", "优化投诉与工单一对一关联查询。"),
    ("idx_ticket_status", "优化按工单状态筛选。"),
    ("idx_ticket_owner", "优化按负责人查询待办。"),
    ("idx_ticket_department", "优化按部门查询工单。"),
    ("idx_assignment_ticket", "优化工单分派历史查询。"),
    ("idx_escalation_ticket", "优化工单升级历史查询。"),
    ("idx_action_ticket", "优化工单处理日志查询。"),
    ("idx_feedback_ticket", "优化工单反馈查询。"),
]

ROLE_PERMISSION_DETAILS = [
    ("admin", "可查看和操作所有数据，可访问统计看板和数据库说明页。"),
    ("manager", "可查看全部工单和统计数据，可分派、升级、关闭与重开工单。"),
    ("customer_service", "可从订单创建投诉，可查看客服范围工单并执行常规分派。"),
    ("finance", "只能查看财务售后部和费用争议相关工单。"),
    ("safety", "只能查看安全事件、安全部及 P1 工单。"),
    ("operation", "只能查看运营部、司机服务和取消争议相关工单。"),
    ("employee", "只能查看本人负责的未关闭工单，可新增处理日志和继续处理。"),
]


ER_ENTITIES = [
    {
        "id": "passenger",
        "title": "Passenger",
        "label": "乘客表",
        "group": "foundation",
        "description": "记录乘客基础信息，是订单、投诉和反馈的来源主体。",
        "pk": "passenger_id",
        "fks": [],
        "fields": ["passenger_name", "phone", "account_status", "created_at"],
        "x": 40,
        "y": 60,
    },
    {
        "id": "driver",
        "title": "Driver",
        "label": "司机表",
        "group": "foundation",
        "description": "记录司机基础信息，为订单和投诉处理提供背景。",
        "pk": "driver_id",
        "fks": [],
        "fields": ["driver_name", "phone", "driver_score", "driver_status", "created_at"],
        "x": 40,
        "y": 260,
    },
    {
        "id": "ride_order",
        "title": "Ride_Order",
        "label": "订单表",
        "group": "foundation",
        "description": "记录简化后的订单背景信息，是投诉来源的业务主线入口。",
        "pk": "order_id",
        "fks": ["passenger_id -> Passenger", "driver_id -> Driver"],
        "fields": ["start_location", "end_location", "order_time", "finish_time", "order_amount", "order_status"],
        "x": 280,
        "y": 160,
    },
    {
        "id": "complaint_type",
        "title": "Complaint_Type",
        "label": "投诉类型表",
        "group": "rule",
        "description": "分类字典和默认规则表，决定默认部门、优先级和 SLA。",
        "pk": "complaint_type_id",
        "fks": ["default_department_id -> Department"],
        "fields": ["type_name", "type_description", "default_priority_level", "default_sla_hours"],
        "x": 280,
        "y": 380,
    },
    {
        "id": "complaint",
        "title": "Complaint",
        "label": "投诉表",
        "group": "case",
        "description": "记录乘客针对订单提出的原始问题，是工单生成的直接来源。",
        "pk": "complaint_id",
        "fks": ["order_id -> Ride_Order", "passenger_id -> Passenger", "complaint_type_id -> Complaint_Type"],
        "fields": ["complaint_content", "complaint_time", "urgency_level", "complaint_status"],
        "x": 540,
        "y": 160,
    },
    {
        "id": "department",
        "title": "Department",
        "label": "部门表",
        "group": "org",
        "description": "定义责任部门和主管，是分派、统计和权限隔离的核心对象。",
        "pk": "department_id",
        "fks": ["manager_id -> Employee"],
        "fields": ["department_name", "department_type"],
        "x": 540,
        "y": 380,
    },
    {
        "id": "employee",
        "title": "Employee",
        "label": "员工表",
        "group": "org",
        "description": "定义系统登录人员、角色、所属部门和处理责任人。",
        "pk": "employee_id",
        "fks": ["department_id -> Department"],
        "fields": ["employee_name", "role", "username", "password", "phone", "employee_status"],
        "x": 780,
        "y": 380,
    },
    {
        "id": "ticket",
        "title": "Ticket",
        "label": "工单表",
        "group": "case",
        "description": "工单闭环主表，记录处理状态、负责人、责任部门和 SLA。",
        "pk": "ticket_id",
        "fks": ["complaint_id -> Complaint", "department_id -> Department", "current_owner_id -> Employee"],
        "fields": ["priority_level", "ticket_status", "create_time", "sla_deadline", "close_time"],
        "x": 800,
        "y": 160,
    },
    {
        "id": "assignment_record",
        "title": "Assignment_Record",
        "label": "分派记录表",
        "group": "process",
        "description": "记录工单从谁分给谁、分到哪个部门，以及分派备注。",
        "pk": "assignment_id",
        "fks": ["ticket_id -> Ticket", "assigner_id -> Employee", "receiver_id -> Employee", "department_id -> Department"],
        "fields": ["assign_time", "assignment_note"],
        "x": 1080,
        "y": 60,
    },
    {
        "id": "escalation_record",
        "title": "Escalation_Record",
        "label": "升级记录表",
        "group": "process",
        "description": "记录复杂、高优先级或超时工单的升级过程。",
        "pk": "escalation_id",
        "fks": ["ticket_id -> Ticket", "escalated_by -> Employee"],
        "fields": ["from_level", "to_level", "escalation_reason", "escalation_time"],
        "x": 1080,
        "y": 220,
    },
    {
        "id": "action_log",
        "title": "Action_Log",
        "label": "处理日志表",
        "group": "process",
        "description": "记录联系乘客、联系司机、核查订单等实际处理动作。",
        "pk": "log_id",
        "fks": ["ticket_id -> Ticket", "employee_id -> Employee"],
        "fields": ["action_type", "action_content", "action_time"],
        "x": 1080,
        "y": 380,
    },
    {
        "id": "feedback",
        "title": "Feedback",
        "label": "反馈表",
        "group": "process",
        "description": "记录乘客满意度和反馈内容，决定工单关闭或重开。",
        "pk": "feedback_id",
        "fks": ["ticket_id -> Ticket", "passenger_id -> Passenger"],
        "fields": ["satisfaction_score", "feedback_content", "feedback_time"],
        "x": 1080,
        "y": 540,
    },
]

ER_RELATION_DETAILS = [
    {
        "id": "rel_passenger_order",
        "from": "passenger",
        "to": "ride_order",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Passenger 1 对多 Ride_Order",
        "description": "一个乘客可以拥有多笔订单，每笔订单只对应一个乘客。",
    },
    {
        "id": "rel_driver_order",
        "from": "driver",
        "to": "ride_order",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Driver 1 对多 Ride_Order",
        "description": "一个司机可以关联多笔订单，每笔订单只对应一个司机。",
    },
    {
        "id": "rel_order_complaint",
        "from": "ride_order",
        "to": "complaint",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Ride_Order 1 对多 Complaint",
        "description": "一笔订单可能被多次投诉，投诉表保存原始问题记录。",
    },
    {
        "id": "rel_passenger_complaint",
        "from": "passenger",
        "to": "complaint",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Passenger 1 对多 Complaint",
        "description": "一个乘客可以提交多条投诉，投诉记录会保留发起乘客。",
    },
    {
        "id": "rel_type_complaint",
        "from": "complaint_type",
        "to": "complaint",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Complaint_Type 1 对多 Complaint",
        "description": "一个投诉类型可以对应多条投诉，用于分类与默认规则匹配。",
    },
    {
        "id": "rel_type_department_default",
        "from": "complaint_type",
        "to": "department",
        "show_in_core": False,
        "cardinality": "N : 1",
        "summary": "Complaint_Type N 对 1 Department",
        "description": "每个投诉类型都会绑定一个默认处理部门，用于自动生成工单时的默认分派规则。",
    },
    {
        "id": "rel_complaint_ticket",
        "from": "complaint",
        "to": "ticket",
        "show_in_core": True,
        "cardinality": "1 : 1",
        "summary": "Complaint 1 对 1 Ticket",
        "description": "每条投诉会自动生成一张工单，工单是后台处理任务实体。",
    },
    {
        "id": "rel_department_employee",
        "from": "department",
        "to": "employee",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Department 1 对多 Employee",
        "description": "一个部门下有多名员工，员工登录和工单处理都归属某部门。",
    },
    {
        "id": "rel_department_manager",
        "from": "department",
        "to": "employee",
        "show_in_core": False,
        "cardinality": "N : 1",
        "summary": "Department N 对 1 Employee(manager)",
        "description": "部门表中的 manager_id 指向主管员工，用于主管视图和部门管理。",
    },
    {
        "id": "rel_department_ticket",
        "from": "department",
        "to": "ticket",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Department 1 对多 Ticket",
        "description": "一个部门可以负责多张工单，工单当前责任部门保存在 Ticket 中。",
    },
    {
        "id": "rel_employee_ticket_owner",
        "from": "employee",
        "to": "ticket",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Employee 1 对多 Ticket(current owner)",
        "description": "工单的 current_owner_id 指向当前负责人，用于个人待办和处理归属。",
    },
    {
        "id": "rel_employee_assignment",
        "from": "employee",
        "to": "assignment_record",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Employee 1 对多 Assignment_Record",
        "description": "员工既可以是分派人，也可以是接收人，形成工单流转历史。",
    },
    {
        "id": "rel_assignment_department",
        "from": "department",
        "to": "assignment_record",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Department 1 对多 Assignment_Record",
        "description": "分派记录会保存接收部门，反映工单流转到哪个责任部门。",
    },
    {
        "id": "rel_employee_action",
        "from": "employee",
        "to": "action_log",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Employee 1 对多 Action_Log",
        "description": "员工可以对其负责的工单持续新增处理日志。",
    },
    {
        "id": "rel_employee_escalation",
        "from": "employee",
        "to": "escalation_record",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Employee 1 对多 Escalation_Record",
        "description": "升级记录中的 escalated_by 指向执行升级操作的员工。",
    },
    {
        "id": "rel_ticket_assignment",
        "from": "ticket",
        "to": "assignment_record",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Ticket 1 对多 Assignment_Record",
        "description": "一张工单在处理过程中可能经历多次分派。",
    },
    {
        "id": "rel_ticket_escalation",
        "from": "ticket",
        "to": "escalation_record",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Ticket 1 对多 Escalation_Record",
        "description": "复杂或超时工单可创建多条升级记录，反映升级链路。",
    },
    {
        "id": "rel_ticket_action",
        "from": "ticket",
        "to": "action_log",
        "show_in_core": True,
        "cardinality": "1 : N",
        "summary": "Ticket 1 对多 Action_Log",
        "description": "工单生命周期中的处理动作会持续沉淀到日志表。",
    },
    {
        "id": "rel_ticket_feedback",
        "from": "ticket",
        "to": "feedback",
        "show_in_core": True,
        "cardinality": "1 : 0..1",
        "summary": "Ticket 1 对 0 或 1 Feedback",
        "description": "每张工单最多有一条反馈记录，反馈决定关闭或重开。",
    },
    {
        "id": "rel_passenger_feedback",
        "from": "passenger",
        "to": "feedback",
        "show_in_core": False,
        "cardinality": "1 : N",
        "summary": "Passenger 1 对多 Feedback",
        "description": "乘客可以针对不同工单留下反馈记录。",
    },
]


def ticket_query_for_user(user: Employee):
    query = (
        Ticket.query.join(Complaint)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .options(
            joinedload(Ticket.complaint).joinedload(Complaint.complaint_type),
            joinedload(Ticket.department),
            joinedload(Ticket.current_owner),
        )
    )
    return apply_ticket_scope(query, user)


def complaint_query_for_user(user: Employee):
    query = (
        Complaint.query.join(Ticket, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .options(
            joinedload(Complaint.complaint_type),
            joinedload(Complaint.order),
            joinedload(Complaint.ticket),
        )
    )
    return apply_complaint_scope(query, user)


@main_bp.route("/")
@login_required
def index():
    ticket_query = ticket_query_for_user(g.current_user)
    stats = get_scope_stats(ticket_query)
    today_start = datetime.combine(datetime.now().date(), datetime.min.time())
    today_new_complaints = complaint_query_for_user(g.current_user).filter(
        Complaint.complaint_time >= today_start
    ).count()
    pending_ticket_count = ticket_query_for_user(g.current_user).filter(
        Ticket.ticket_status != "已关闭"
    ).count()
    my_pending_count = Ticket.query.filter(
        Ticket.current_owner_id == g.current_user.employee_id,
        Ticket.ticket_status != "已关闭",
    ).count()

    preview_title = ""
    preview_rows = []
    preview_columns = []

    if g.current_user.role in {"admin", "manager"}:
        preview_title = "主管统计视图"
        result = db.session.execute(text("SELECT * FROM v_manager_ticket_summary ORDER BY department_name"))
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("department_name", "部门"),
            ("total_tickets", "工单总数"),
            ("processing_tickets", "处理中"),
            ("closed_tickets", "已关闭"),
            ("overdue_tickets", "超时"),
        ]
    elif g.current_user.role == "customer_service":
        preview_title = "客服工单视图"
        result = db.session.execute(
            text("SELECT * FROM v_customer_service_ticket ORDER BY sla_deadline LIMIT 5")
        )
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("ticket_id", "工单编号"),
            ("order_id", "订单编号"),
            ("passenger_name", "乘客"),
            ("type_name", "投诉类型"),
            ("ticket_status", "工单状态"),
            ("priority_level", "优先级"),
        ]
    elif g.current_user.role == "finance":
        preview_title = "财务售后视图"
        result = db.session.execute(
            text("SELECT * FROM v_finance_complaint_ticket ORDER BY sla_deadline LIMIT 5")
        )
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("ticket_id", "工单编号"),
            ("order_id", "订单编号"),
            ("type_name", "投诉类型"),
            ("priority_level", "优先级"),
            ("ticket_status", "状态"),
            ("department_name", "责任部门"),
        ]
    elif g.current_user.role == "safety":
        preview_title = "安全工单视图"
        result = db.session.execute(text("SELECT * FROM v_safety_ticket ORDER BY sla_deadline LIMIT 5"))
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("ticket_id", "工单编号"),
            ("order_id", "订单编号"),
            ("type_name", "投诉类型"),
            ("priority_level", "优先级"),
            ("ticket_status", "状态"),
            ("sla_deadline", "SLA 截止时间"),
        ]
    elif g.current_user.role == "operation":
        preview_title = "运营工单视图"
        result = db.session.execute(
            text("SELECT * FROM v_operation_ticket ORDER BY sla_deadline LIMIT 5")
        )
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("ticket_id", "工单编号"),
            ("order_id", "订单编号"),
            ("type_name", "投诉类型"),
            ("priority_level", "优先级"),
            ("ticket_status", "状态"),
            ("department_name", "责任部门"),
        ]
    else:
        preview_title = "员工待办视图"
        result = db.session.execute(
            text(
                """
                SELECT * FROM v_employee_pending_ticket
                WHERE current_owner_id = :owner_id
                ORDER BY sla_deadline
                LIMIT 5
                """
            ),
            {"owner_id": g.current_user.employee_id},
        )
        preview_rows = [dict(row._mapping) for row in result]
        preview_columns = [
            ("ticket_id", "工单编号"),
            ("type_name", "投诉类型"),
            ("priority_level", "优先级"),
            ("ticket_status", "状态"),
            ("sla_deadline", "SLA 截止时间"),
        ]

    quick_actions = []
    if can_create_complaint(g.current_user):
        quick_actions.append(
            {
                "title": "新增投诉",
                "description": "从订单详情发起投诉登记，系统会自动生成工单。",
                "endpoint": "main.orders",
                "style": "primary",
            }
        )
    quick_actions.append(
        {
            "title": "查看工单",
            "description": "进入工单中心查看处理进度、责任部门和闭环状态。",
            "endpoint": "main.tickets",
            "style": "info",
        }
    )
    if g.current_user.role == "employee" or my_pending_count > 0:
        quick_actions.append(
            {
                "title": "我的待办",
                "description": "查看当前负责人为自己的未关闭工单并继续处理。",
                "endpoint": "main.tickets",
                "style": "secondary",
            }
        )
    if g.current_user.role in {"admin", "manager"}:
        quick_actions.append(
            {
                "title": "统计看板",
                "description": "查看部门统计、优先级分布、满意度和 P1 工单。",
                "endpoint": "main.dashboard",
                "style": "warning",
            }
        )

    return render_template(
        "index.html",
        stats=stats,
        today_new_complaints=today_new_complaints,
        pending_ticket_count=pending_ticket_count,
        my_pending_count=my_pending_count,
        quick_actions=quick_actions,
        preview_title=preview_title,
        preview_rows=preview_rows,
        preview_columns=preview_columns,
        scope_name=get_role_scope_name(g.current_user.role),
    )


@main_bp.route("/orders")
@login_required
def orders():
    search_query = request.args.get("q", "").strip()
    query = RideOrder.query.join(Passenger).join(Driver).options(
        joinedload(RideOrder.passenger),
        joinedload(RideOrder.driver),
    )
    if search_query:
        pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                RideOrder.order_id.like(pattern),
                Passenger.passenger_name.like(pattern),
                Driver.driver_name.like(pattern),
            )
        )
    orders = query.order_by(RideOrder.order_time.desc()).all()
    return render_template(
        "orders.html",
        orders=orders,
        can_create_complaint=can_create_complaint(g.current_user),
        search_query=search_query,
    )


@main_bp.route("/orders/<order_id>")
@login_required
def order_detail(order_id):
    order = (
        RideOrder.query.options(
            joinedload(RideOrder.passenger),
            joinedload(RideOrder.driver),
            joinedload(RideOrder.complaints).joinedload(Complaint.complaint_type),
            joinedload(RideOrder.complaints).joinedload(Complaint.ticket),
        )
        .filter_by(order_id=order_id)
        .first_or_404()
    )
    return render_template(
        "order_detail.html",
        order=order,
        can_create_complaint=can_create_complaint(g.current_user),
    )


@main_bp.route("/complaints")
@login_required
def complaints():
    complaints = complaint_query_for_user(g.current_user).order_by(Complaint.complaint_time.desc()).all()
    return render_template("complaints.html", complaints=complaints)


@main_bp.route("/complaints/new", methods=["GET", "POST"])
@login_required
def complaint_new():
    if not can_create_complaint(g.current_user):
        abort(403)

    order_id = request.args.get("order_id") or request.form.get("order_id")
    order_query = RideOrder.query.options(joinedload(RideOrder.passenger), joinedload(RideOrder.driver))
    order = order_query.filter_by(order_id=order_id).first() if order_id else None
    selectable_orders = order_query.order_by(RideOrder.order_time.desc()).limit(20).all()

    complaint_types = ComplaintType.query.order_by(ComplaintType.type_name).all()
    if request.method == "POST":
        complaint_type_id = request.form.get("complaint_type_id", "").strip()
        urgency_level = request.form.get("urgency_level", "").strip()
        complaint_content = request.form.get("complaint_content", "").strip()
        if order is None:
            flash("请先选择有效订单。", "danger")
            return render_template(
                "complaint_new.html",
                order=None,
                selectable_orders=selectable_orders,
                complaint_types=complaint_types,
                urgency_choices=URGENCY_CHOICES,
            )

        complaint_type = ComplaintType.query.filter_by(complaint_type_id=complaint_type_id).first()
        if not complaint_type or urgency_level not in URGENCY_CHOICES or not complaint_content:
            flash("请完整填写投诉信息。", "danger")
        else:
            complaint, ticket = create_complaint_and_ticket(
                order,
                complaint_type,
                complaint_content,
                urgency_level,
            )
            db.session.commit()
            flash(f"投诉已创建，并自动生成工单 {ticket.ticket_id}。", "success")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket.ticket_id))

    return render_template(
        "complaint_new.html",
        order=order,
        selectable_orders=selectable_orders,
        complaint_types=complaint_types,
        urgency_choices=URGENCY_CHOICES,
    )


@main_bp.route("/tickets")
@login_required
def tickets():
    query = ticket_query_for_user(g.current_user)

    status = request.args.get("status", "").strip()
    department_id = request.args.get("department_id", "").strip()
    priority = request.args.get("priority", "").strip()
    complaint_type_id = request.args.get("complaint_type_id", "").strip()

    if status in TICKET_STATUS_CHOICES:
        query = query.filter(Ticket.ticket_status == status)
    if department_id:
        query = query.filter(Ticket.department_id == department_id)
    if priority in PRIORITY_CHOICES:
        query = query.filter(Ticket.priority_level == priority)
    if complaint_type_id:
        query = query.filter(Complaint.complaint_type_id == complaint_type_id)

    tickets = query.order_by(Ticket.create_time.desc()).all()
    departments = get_accessible_departments(g.current_user)
    complaint_types = ComplaintType.query.order_by(ComplaintType.type_name).all()
    page_title = {
        "finance": "财务售后工单",
        "safety": "安全工单",
        "operation": "运营工单",
        "employee": "我的待办工单",
    }.get(g.current_user.role, "工单管理")
    page_subtitle = {
        "finance": "展示财务售后部和费用争议相关工单。",
        "safety": "展示安全事件、安全部和 P1 高优先级工单。",
        "operation": "展示运营部、司机服务和取消争议相关工单。",
        "employee": "仅展示当前负责人为自己的未关闭工单。",
    }.get(g.current_user.role, f"当前数据权限范围：{get_role_scope_name(g.current_user.role)}")

    return render_template(
        "tickets.html",
        tickets=tickets,
        departments=departments,
        complaint_types=complaint_types,
        ticket_statuses=TICKET_STATUS_CHOICES,
        priority_choices=PRIORITY_CHOICES,
        filters={
            "status": status,
            "department_id": department_id,
            "priority": priority,
            "complaint_type_id": complaint_type_id,
        },
        scope_name=get_role_scope_name(g.current_user.role),
        page_title=page_title,
        page_subtitle=page_subtitle,
    )


@main_bp.route("/tickets/<ticket_id>")
@login_required
def ticket_detail(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = (
        Ticket.query.options(
            joinedload(Ticket.complaint).joinedload(Complaint.complaint_type),
            joinedload(Ticket.complaint).joinedload(Complaint.passenger),
            joinedload(Ticket.complaint).joinedload(Complaint.order).joinedload(RideOrder.driver),
            joinedload(Ticket.complaint).joinedload(Complaint.order).joinedload(RideOrder.passenger),
            joinedload(Ticket.department),
            joinedload(Ticket.current_owner),
            joinedload(Ticket.assignments).joinedload(AssignmentRecord.assigner),
            joinedload(Ticket.assignments).joinedload(AssignmentRecord.receiver),
            joinedload(Ticket.assignments).joinedload(AssignmentRecord.department),
            joinedload(Ticket.escalations).joinedload(EscalationRecord.escalated_by_employee),
            joinedload(Ticket.action_logs).joinedload(ActionLog.employee),
            joinedload(Ticket.feedback).joinedload(Feedback.passenger),
        )
        .filter_by(ticket_id=ticket_id)
        .first_or_404()
    )

    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        can_assign=can_assign_ticket(g.current_user, ticket),
        can_escalate=can_escalate_ticket(g.current_user, ticket),
        can_add_log_flag=can_add_log(g.current_user, ticket),
        can_pending_feedback=can_set_pending_feedback(g.current_user, ticket)
        and ticket.ticket_status not in {"待反馈", "已关闭"},
        can_feedback=can_submit_feedback(g.current_user, ticket)
        and ticket.ticket_status == "待反馈"
        and ticket.feedback is None,
        can_reopen=can_reopen_ticket(g.current_user, ticket)
        and ticket.ticket_status in {"已关闭", "待反馈"},
    )


@main_bp.route("/tickets/<ticket_id>/assign", methods=["GET", "POST"])
@login_required
def assign_ticket_view(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = Ticket.query.options(joinedload(Ticket.department)).filter_by(ticket_id=ticket_id).first_or_404()
    if not can_assign_ticket(g.current_user, ticket):
        abort(403)

    employees = (
        Employee.query.options(joinedload(Employee.department))
        .filter_by(employee_status="在职")
        .order_by(Employee.department_id, Employee.employee_name)
        .all()
    )

    if request.method == "POST":
        receiver_id = request.form.get("receiver_id", "").strip()
        note = request.form.get("assignment_note", "").strip()
        receiver = Employee.query.filter_by(employee_id=receiver_id, employee_status="在职").first()
        if not receiver:
            flash("请选择有效的接收员工。", "danger")
        else:
            assign_ticket(ticket, g.current_user, receiver, note)
            db.session.commit()
            flash("工单已分派。", "success")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))

    return render_template("assign_ticket.html", ticket=ticket, employees=employees)


@main_bp.route("/tickets/<ticket_id>/escalate", methods=["GET", "POST"])
@login_required
def escalate_ticket_view(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = Ticket.query.options(joinedload(Ticket.department)).filter_by(ticket_id=ticket_id).first_or_404()
    if not can_escalate_ticket(g.current_user, ticket):
        abort(403)

    default_from_level = ticket.department.department_name if ticket.department else "一线处理"

    if request.method == "POST":
        from_level = request.form.get("from_level", default_from_level).strip()
        to_level = request.form.get("to_level", "").strip()
        reason = request.form.get("escalation_reason", "").strip()
        if not from_level or not to_level or not reason:
            flash("请完整填写升级信息。", "danger")
        else:
            escalate_ticket(ticket, g.current_user, from_level, to_level, reason)
            db.session.commit()
            flash("工单已升级。", "success")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))

    return render_template(
        "escalate_ticket.html",
        ticket=ticket,
        default_from_level=default_from_level,
        to_level_options=TO_LEVEL_OPTIONS,
    )


@main_bp.route("/tickets/<ticket_id>/log", methods=["GET", "POST"])
@login_required
def add_log_view(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = Ticket.query.filter_by(ticket_id=ticket_id).first_or_404()
    if not can_add_log(g.current_user, ticket):
        abort(403)

    if request.method == "POST":
        action_type = request.form.get("action_type", "").strip()
        action_content = request.form.get("action_content", "").strip()
        if action_type not in ACTION_TYPES or not action_content:
            flash("请填写完整的处理日志。", "danger")
        else:
            try:
                add_action_log(ticket, g.current_user, action_type, action_content)
                db.session.commit()
                flash("处理日志已添加。", "success")
                return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

    return render_template("add_log.html", ticket=ticket, action_types=ACTION_TYPES)


@main_bp.route("/tickets/<ticket_id>/pending-feedback", methods=["POST"])
@login_required
def pending_feedback(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = Ticket.query.filter_by(ticket_id=ticket_id).first_or_404()
    if not can_set_pending_feedback(g.current_user, ticket):
        abort(403)

    try:
        set_ticket_pending_feedback(ticket, g.current_user)
        db.session.commit()
        flash("工单已转为待反馈。", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))


@main_bp.route("/tickets/<ticket_id>/feedback", methods=["GET", "POST"])
@login_required
def feedback_view(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = (
        Ticket.query.options(
            joinedload(Ticket.complaint).joinedload(Complaint.passenger),
            joinedload(Ticket.feedback),
        )
        .filter_by(ticket_id=ticket_id)
        .first_or_404()
    )
    if not can_submit_feedback(g.current_user, ticket):
        abort(403)
    if ticket.feedback is not None:
        flash("该工单已有反馈记录。", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))
    if ticket.ticket_status != "待反馈":
        flash("只有待反馈工单才能提交反馈。", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))

    if request.method == "POST":
        try:
            score = int(request.form.get("satisfaction_score", "0"))
        except ValueError:
            score = 0
        feedback_content = request.form.get("feedback_content", "").strip()

        if not feedback_content:
            flash("请填写反馈内容。", "danger")
        else:
            try:
                submit_feedback(ticket, score, feedback_content)
                db.session.commit()
                flash("反馈已提交。", "success")
                return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

    return render_template("feedback.html", ticket=ticket)


@main_bp.route("/tickets/<ticket_id>/reopen", methods=["POST"])
@login_required
def reopen_ticket_view(ticket_id):
    require_ticket_access(g.current_user, ticket_id)
    ticket = Ticket.query.filter_by(ticket_id=ticket_id).first_or_404()
    if not can_reopen_ticket(g.current_user, ticket):
        abort(403)
    if ticket.ticket_status not in {"已关闭", "待反馈"}:
        flash("当前状态不支持直接重开。", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))
    if ticket.ticket_status == "已重开":
        flash("工单已经是已重开状态。", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))

    reopen_ticket(ticket, g.current_user)
    db.session.commit()
    flash("工单已重开。", "success")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    if g.current_user.role not in {"admin", "manager"}:
        abort(403)

    total_tickets = Ticket.query.count()
    processing_count = Ticket.query.filter(
        Ticket.ticket_status.in_(["待分派", "处理中", "已升级", "待反馈", "已重开"])
    ).count()
    closed_count = Ticket.query.filter(Ticket.ticket_status == "已关闭").count()
    department_summary = [
        dict(row._mapping)
        for row in db.session.execute(text("SELECT * FROM v_manager_ticket_summary ORDER BY department_name"))
    ]
    complaint_type_counts = (
        db.session.query(ComplaintType.type_name, func.count(Complaint.complaint_id))
        .outerjoin(Complaint, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .group_by(ComplaintType.type_name)
        .order_by(func.count(Complaint.complaint_id).desc(), ComplaintType.type_name)
        .all()
    )
    priority_counts = (
        db.session.query(Ticket.priority_level, func.count(Ticket.ticket_id))
        .group_by(Ticket.priority_level)
        .order_by(Ticket.priority_level)
        .all()
    )
    avg_satisfaction = db.session.query(func.avg(Feedback.satisfaction_score)).scalar()
    overdue_count = Ticket.query.filter(
        Ticket.ticket_status != "已关闭",
        Ticket.sla_deadline < datetime.now(),
    ).count()
    p1_tickets = (
        Ticket.query.options(
            joinedload(Ticket.complaint).joinedload(Complaint.complaint_type),
            joinedload(Ticket.department),
            joinedload(Ticket.current_owner),
        )
        .filter(Ticket.priority_level == "P1")
        .order_by(Ticket.sla_deadline)
        .all()
    )
    feedback_rows = [
        dict(row._mapping)
        for row in db.session.execute(text("SELECT * FROM v_feedback_result ORDER BY feedback_time DESC LIMIT 8"))
    ]

    return render_template(
        "dashboard.html",
        dashboard_stats={
            "total_tickets": total_tickets,
            "processing_count": processing_count,
            "closed_count": closed_count,
            "overdue_count": overdue_count,
            "avg_satisfaction": round(float(avg_satisfaction), 2) if avg_satisfaction else None,
        },
        department_summary=department_summary,
        complaint_type_counts=complaint_type_counts,
        priority_counts=priority_counts,
        p1_tickets=p1_tickets,
        feedback_rows=feedback_rows,
    )


@main_bp.route("/schema")
@login_required
def schema():
    return render_template(
        "schema.html",
        schema_tables=SCHEMA_TABLES,
        workflow_steps=WORKFLOW_STEPS,
        core_relationships=CORE_RELATIONSHIPS,
        er_entities=ER_ENTITIES,
        er_relations=ER_RELATION_DETAILS,
        schema_views=SCHEMA_VIEWS,
        schema_view_details=SCHEMA_VIEW_DETAILS,
        schema_indexes=SCHEMA_INDEXES,
        schema_index_details=SCHEMA_INDEX_DETAILS,
        role_permission_details=ROLE_PERMISSION_DETAILS,
    )
