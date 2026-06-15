from __future__ import annotations

from datetime import date, datetime, timedelta

import ai_service
from ai_service import detect_action_request, handle_ai_action, handle_ai_chat
from database import db
from models import ActionLog, AssignmentRecord, Complaint, ComplaintType, Employee, EscalationRecord, Feedback, RideOrder, Ticket


def _setup_mock_ai(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "get_ai_runtime_status",
        lambda: {"available": True, "model_name": "mock", "message": "mock"},
    )
    monkeypatch.setattr(ai_service, "_call_intent_model", lambda message, context: ai_service._rule_based_intent(message, context))
    monkeypatch.setattr(ai_service, "_call_plan_model", lambda message, context: ai_service._rule_based_plan(message, context))
    monkeypatch.setattr(ai_service, "_call_action_model", lambda message, context: ai_service._rule_based_action_safe(message, context))
    monkeypatch.setattr(ai_service, "_call_summary_model", lambda *args, **kwargs: "summary")


def _run_command(user: Employee, command: str):
    fn = handle_ai_action if detect_action_request(command) else handle_ai_chat
    return fn(command, user)


def test_command_matrix_core(monkeypatch, app_ctx):
    _setup_mock_ai(monkeypatch)
    admin = Employee.query.filter_by(username="admin").first()

    first_complaint = Complaint.query.order_by(Complaint.complaint_time.asc(), Complaint.complaint_id.asc()).first()
    first_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).first()

    result = _run_command(admin, "查询订单 ORD001 的完整信息，包括乘客和司机是谁")
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["rows"][0]["order_id"] == "ORD001"
    assert result["rows"][0]["passenger_name"] == "张敏"
    assert result["rows"][0]["driver_name"] == "刘强"

    result = _run_command(admin, "查一下最近一周所有已完成订单")
    today = date.today()
    expected_recent_completed = RideOrder.query.filter(
        RideOrder.order_status == "已完成",
        RideOrder.order_time >= today - timedelta(days=7),
        RideOrder.order_time < today + timedelta(days=1),
    ).count()
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["data_count"] == expected_recent_completed

    result = _run_command(admin, "查询乘客 P001 的所有历史订单")
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert all(row["order_id"].startswith("ORD") for row in result["rows"])
    assert all(row["passenger_name"] == "张敏" for row in result["rows"])

    result = _run_command(admin, "查询司机 D010 的接单数量和完成率")
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["data_count"] in {0, 1}

    result = _run_command(admin, "查订单 ORD002 是否产生过投诉")
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["rows"][0]["order_id"] == "ORD002"
    assert result["rows"][0]["has_complaint"] is True
    assert result["rows"][0]["complaint_count"] >= 1

    result = _run_command(admin, "查询订单金额大于 100 元的所有订单")
    expected_high_amount = RideOrder.query.filter(RideOrder.order_amount >= 100).count()
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["data_count"] == expected_high_amount

    result = _run_command(admin, "查询今天新增的订单列表")
    today_orders = RideOrder.query.filter(RideOrder.order_time >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).count()
    assert result["ok"] is True
    assert result["intent"] == "order_query"
    assert result["data_count"] == today_orders

    result = _run_command(admin, "查询订单 ORD001 对应的投诉列表")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert result["rows"][0]["order_id"] == "ORD001"

    result = _run_command(admin, "查询投诉 ID C001 的详细内容")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert result["rows"][0]["complaint_id"] == first_complaint.complaint_id

    result = _run_command(admin, "查询所有服务态度类型的投诉")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert all(row["complaint_type"] == "司机服务" for row in result["rows"])

    result = _run_command(admin, "查询投诉状态为待处理的投诉列表")
    expected_pending_complaints = Complaint.query.filter(Complaint.complaint_status != "已关闭").count()
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert result["data_count"] == expected_pending_complaints

    result = _run_command(admin, "查询投诉类型对应的默认部门和 SLA")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert "default_department_name" in result["rows"][0]
    assert "default_sla_hours" in result["rows"][0]

    result = _run_command(admin, "查询工单 T001 的当前状态")
    assert result["ok"] is True
    assert result["intent"] in {"ticket_detail", "ticket_query"}
    assert result["rows"][0]["ticket_id"] == first_ticket.ticket_id

    result = _run_command(admin, "查询所有未关闭工单")
    expected_open_tickets = Ticket.query.filter(Ticket.ticket_status != "已关闭").count()
    assert result["ok"] is True, result
    assert result["intent"] in {"ticket_query", "risk_query"}
    assert result["data_count"] == expected_open_tickets

    result = _run_command(admin, "查询 SLA 即将超时的工单")
    expected_near_sla = Ticket.query.filter(
        Ticket.ticket_status != "已关闭",
        Ticket.sla_deadline >= datetime.now(),
        Ticket.sla_deadline <= datetime.now() + timedelta(hours=24),
    ).count()
    assert result["ok"] is True
    assert result["intent"] in {"ticket_query", "risk_query"}
    assert result["data_count"] == expected_near_sla

    result = _run_command(admin, "查询部门 运营部 的所有工单")
    expected_operation_tickets = Ticket.query.filter(Ticket.department.has(department_name="运营部")).count()
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_operation_tickets
    assert all(row["department_name"] == "运营部" for row in result["rows"])

    result = _run_command(admin, "查询员工 E001 负责的工单列表")
    expected_emp001 = Ticket.query.filter(Ticket.current_owner_id == "EMP001").count()
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_emp001

    result = _run_command(admin, "查询优先级为 P1 的工单")
    expected_p1 = Ticket.query.filter(Ticket.priority_level == "P1").count()
    assert result["ok"] is True
    assert result["intent"] in {"ticket_query", "risk_query"}
    assert result["data_count"] == expected_p1
    assert all(row["priority_level"] == "P1" for row in result["rows"])

    result = _run_command(admin, "查询工单 T002 的生命周期记录")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] >= 1
    assert result["rows"][0]["ticket_id"].startswith("TCK")

    result = _run_command(admin, "查询所有已升级工单")
    expected_upgraded = Ticket.query.filter(Ticket.ticket_status == "已升级").count()
    assert result["ok"] is True
    assert result["intent"] in {"ticket_query", "risk_query"}
    assert result["data_count"] == expected_upgraded

    result = _run_command(admin, "查询工单 T001 的分派历史")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["rows"][0]["ticket_id"] == first_ticket.ticket_id
    assert "assignment_id" in result["rows"][0]

    result = _run_command(admin, "查询所有今天发生的分派记录")
    expected_today_assignments = AssignmentRecord.query.filter(
        AssignmentRecord.assign_time >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ).count()
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_today_assignments

    result = _run_command(admin, "查询所有已升级到主管层的工单")
    expected_manager_level = EscalationRecord.query.filter(EscalationRecord.to_level == "主管复核").count()
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_manager_level

    result = _run_command(admin, "查询工单 T001 的处理日志")
    expected_logs = ActionLog.query.filter_by(ticket_id=first_ticket.ticket_id).count()
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_logs

    result = _run_command(admin, "查询工单 T001 的用户满意度反馈")
    assert result["ok"] is True
    assert result["intent"] == "feedback_query"
    assert result["rows"][0]["ticket_id"] == first_ticket.ticket_id

    result = _run_command(admin, "查询所有评分低于 3 的反馈")
    expected_low_feedback = Feedback.query.filter(Feedback.satisfaction_score < 3).count()
    assert result["ok"] is True
    assert result["intent"] == "feedback_query"
    assert result["data_count"] == expected_low_feedback

    result = _run_command(admin, "查询反馈与投诉类型的统计关系")
    assert result["ok"] is True
    assert result["intent"] == "feedback_query"
    assert "avg_satisfaction_score" in result["rows"][0]
    assert "feedback_count" in result["rows"][0]

    complaints_before = Complaint.query.count()
    tickets_before = Ticket.query.count()
    result = _run_command(admin, "创建一条投诉：订单 ORD002 司机态度差")
    assert result["ok"] is True
    assert result["intent"] == "create_complaint"
    assert Complaint.query.count() == complaints_before + 1
    assert Ticket.query.count() == tickets_before + 1

    result = _run_command(admin, "创建一条投诉：订单 ORD003 多收费")
    assert result["ok"] is True
    assert result["intent"] == "create_complaint"

    fifth_complaint_id = Complaint.query.order_by(Complaint.complaint_time.asc(), Complaint.complaint_id.asc()).offset(4).first().complaint_id
    result = _run_command(admin, "修改投诉 C005 的紧急程度为高")
    assert result["ok"] is True
    assert result["intent"] == "update_complaint_urgency"
    assert result["rows"][0]["complaint_id"] == fifth_complaint_id
    assert result["rows"][0]["urgency_level"] == "U1"

    result = _run_command(admin, "删除投诉 C010（无效投诉）")
    assert result["ok"] is False
    assert result["error_code"] == "VALIDATION_ERROR"

    result = _run_command(admin, "创建工单（基于投诉 C002）")
    assert result["ok"] is True
    assert result["intent"] == "create_ticket_for_complaint"
    assert result["rows"][0]["complaint_id"].startswith("CMP")

    third_ticket_id = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(2).first().ticket_id
    result = _run_command(admin, "修改工单 T003 优先级为 P1")
    assert result["ok"] is True
    assert result["intent"] == "update_ticket_priority"
    assert result["rows"][0]["ticket_id"] == third_ticket_id
    assert result["rows"][0]["priority_level"] == "P1"

    tenth_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(9).first()
    result = _run_command(admin, "关闭工单 T010")
    assert result["ok"] is True
    assert result["intent"] == "close_ticket"
    db.session.refresh(tenth_ticket)
    assert tenth_ticket.ticket_status == "已关闭"

    eighth_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(7).first()
    result = _run_command(admin, "重开工单 T008")
    assert result["ok"] is True
    assert result["intent"] == "reopen_ticket"
    db.session.refresh(eighth_ticket)
    assert eighth_ticket.ticket_status == "已重开"

    second_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(1).first()
    result = _run_command(admin, "给工单 T002 分派给员工 E003")
    assert result["ok"] is True
    assert result["intent"] == "assign_ticket"
    db.session.refresh(second_ticket)
    assert second_ticket.current_owner_id == "EMP003"

    result = _run_command(admin, "将工单 T002 从 L1 升级到 L2")
    assert result["ok"] is True
    assert result["intent"] == "escalate_ticket"
    assert result["rows"][0]["to_level"] == "主管复核"

    result = _run_command(admin, "给工单 T003 添加一条处理记录：已联系乘客")
    assert result["ok"] is True
    assert result["intent"] == "add_action_log"
    assert "log_id" in result["rows"][0]

    result = _run_command(admin, "提交工单 T002 的反馈：评分 5 星")
    assert result["ok"] is True
    assert result["intent"] == "submit_feedback"
    assert result["rows"][0]["satisfaction_score"] == 5


def test_command_matrix_extended(monkeypatch, app_ctx):
    _setup_mock_ai(monkeypatch)
    admin = Employee.query.filter_by(username="admin").first()

    result = _run_command(admin, "更新订单 ORD003 的状态为“已完成”")
    assert result["ok"] is True
    assert result["intent"] == "update_order_status"
    assert result["rows"][0]["order_id"] == "ORD003"
    assert result["rows"][0]["order_status"] == "已完成"

    result = _run_command(admin, "删除订单 ORD999（测试数据）")
    assert result["ok"] is False
    assert result["error_code"] == "VALIDATION_ERROR"

    result = _run_command(admin, "查询某个订单 ORD001 下的所有投诉")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert result["rows"][0]["order_id"] == "ORD001"

    today = date.today()
    expected_recent_complaints = Complaint.query.filter(
        Complaint.complaint_time >= today - timedelta(days=1),
        Complaint.complaint_time < today + timedelta(days=1),
    ).count()
    result = _run_command(admin, "查询最近 24 小时新增投诉")
    assert result["ok"] is True
    assert result["intent"] == "complaint_query"
    assert result["data_count"] == expected_recent_complaints

    result = _run_command(admin, "查询某员工最近分派的工单")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert "请补充员工编号或姓名" in result["answer"]

    fifth_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(4).first()
    result = _run_command(admin, "将工单 T005 从张三转派给李四")
    assert result["ok"] is True
    assert result["intent"] == "assign_ticket"
    db.session.refresh(fifth_ticket)
    assert fifth_ticket.current_owner_id == "EMP003"

    sixth_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(5).first()
    assignments_before = AssignmentRecord.query.filter_by(ticket_id=sixth_ticket.ticket_id).count()
    result = _run_command(admin, "撤销工单 T006 的最新一次分派")
    assert result["ok"] is True
    assert result["intent"] == "revoke_assignment"
    assert AssignmentRecord.query.filter_by(ticket_id=sixth_ticket.ticket_id).count() == assignments_before - 1

    first_ticket = Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).first()
    expected_escalations_first = EscalationRecord.query.filter_by(ticket_id=first_ticket.ticket_id).count()
    result = _run_command(admin, "查询工单 T001 是否被升级过")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_escalations_first

    result = _run_command(admin, "查询某工单的升级原因记录")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert "请补充工单编号" in result["answer"]

    expected_emp002_logs = ActionLog.query.filter(
        ActionLog.employee_id == "EMP002",
        ActionLog.action_time >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
    ).count()
    result = _run_command(admin, "查询员工 E002 今天的所有操作日志")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert result["data_count"] == expected_emp002_logs

    result = _run_command(admin, "查询某工单所有历史操作时间线")
    assert result["ok"] is True
    assert result["intent"] == "ticket_query"
    assert "请补充工单编号" in result["answer"]
