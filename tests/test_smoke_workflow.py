from __future__ import annotations

from database import db
from models import ActionLog, Complaint, ComplaintType, Employee, EscalationRecord, Feedback, RideOrder, Ticket
from tests.conftest import login_as


def _create_ticket_via_complaint(client, order_id: str, complaint_type_id: str, content: str, urgency_level: str = "U2"):
    response = client.post(
        "/complaints/new",
        data={
            "order_id": order_id,
            "complaint_type_id": complaint_type_id,
            "urgency_level": urgency_level,
            "complaint_content": content,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    complaint = Complaint.query.filter_by(complaint_content=content).order_by(Complaint.complaint_time.desc()).first()
    assert complaint is not None
    ticket = Ticket.query.filter_by(complaint_id=complaint.complaint_id).first()
    assert ticket is not None
    return complaint, ticket


def test_end_to_end_ticket_workflow(app, app_ctx):
    client = app.test_client()
    login_as(client, "admin", "admin123")

    order = RideOrder.query.order_by(RideOrder.order_time.desc()).first()
    complaint_type = ComplaintType.query.filter_by(type_name="其他问题").first()
    receiver = Employee.query.filter_by(username="employee").first()

    complaint, ticket = _create_ticket_via_complaint(
        client,
        order.order_id,
        complaint_type.complaint_type_id,
        "烟测创建投诉：正向闭环场景。",
    )
    assert complaint.order_id == order.order_id
    assert ticket.complaint_id == complaint.complaint_id

    assign_response = client.post(
        f"/tickets/{ticket.ticket_id}/assign",
        data={
            "receiver_id": receiver.employee_id,
            "assignment_note": "烟测分派给默认员工。",
        },
        follow_redirects=True,
    )
    assert assign_response.status_code == 200
    db_ticket = db.session.get(Ticket, ticket.ticket_id)
    assert db_ticket.current_owner_id == receiver.employee_id
    assert db_ticket.department_id == receiver.department_id
    assert db_ticket.ticket_status == "处理中"

    log_response = client.post(
        f"/tickets/{ticket.ticket_id}/log",
        data={
            "action_type": "核查订单",
            "action_content": "烟测新增处理日志。",
        },
        follow_redirects=True,
    )
    assert log_response.status_code == 200
    assert ActionLog.query.filter_by(ticket_id=ticket.ticket_id, action_content="烟测新增处理日志。").count() == 1

    escalate_response = client.post(
        f"/tickets/{ticket.ticket_id}/escalate",
        data={
            "from_level": "一线处理",
            "to_level": "主管复核",
            "escalation_reason": "烟测需要验证升级流程。",
        },
        follow_redirects=True,
    )
    assert escalate_response.status_code == 200
    db_ticket = db.session.get(Ticket, ticket.ticket_id)
    assert db_ticket.ticket_status == "已升级"
    assert EscalationRecord.query.filter_by(ticket_id=ticket.ticket_id).count() >= 1

    pending_response = client.post(
        f"/tickets/{ticket.ticket_id}/pending-feedback",
        follow_redirects=True,
    )
    assert pending_response.status_code == 200
    db_ticket = db.session.get(Ticket, ticket.ticket_id)
    assert db_ticket.ticket_status == "待反馈"

    feedback_response = client.post(
        f"/tickets/{ticket.ticket_id}/feedback",
        data={
            "satisfaction_score": "5",
            "feedback_content": "烟测正向反馈：处理满意。",
        },
        follow_redirects=True,
    )
    assert feedback_response.status_code == 200
    db_ticket = db.session.get(Ticket, ticket.ticket_id)
    assert db_ticket.ticket_status == "已关闭"
    assert Feedback.query.filter_by(ticket_id=ticket.ticket_id, satisfaction_score=5).count() == 1


def test_low_score_feedback_reopens_ticket(app, app_ctx):
    client = app.test_client()
    login_as(client, "admin", "admin123")

    order = RideOrder.query.order_by(RideOrder.order_time.asc()).first()
    complaint_type = ComplaintType.query.filter_by(type_name="其他问题").first()
    receiver = Employee.query.filter_by(username="employee").first()

    _, ticket = _create_ticket_via_complaint(
        client,
        order.order_id,
        complaint_type.complaint_type_id,
        "烟测创建投诉：低满意度重开场景。",
        urgency_level="U3",
    )

    client.post(
        f"/tickets/{ticket.ticket_id}/assign",
        data={
            "receiver_id": receiver.employee_id,
            "assignment_note": "烟测分派给默认员工。",
        },
        follow_redirects=True,
    )
    client.post(
        f"/tickets/{ticket.ticket_id}/log",
        data={
            "action_type": "联系乘客",
            "action_content": "烟测低分场景先补日志。",
        },
        follow_redirects=True,
    )
    client.post(
        f"/tickets/{ticket.ticket_id}/pending-feedback",
        follow_redirects=True,
    )
    feedback_response = client.post(
        f"/tickets/{ticket.ticket_id}/feedback",
        data={
            "satisfaction_score": "2",
            "feedback_content": "烟测低满意度反馈：要求继续处理。",
        },
        follow_redirects=True,
    )
    assert feedback_response.status_code == 200

    db_ticket = db.session.get(Ticket, ticket.ticket_id)
    assert db_ticket.ticket_status == "已重开"
    assert Feedback.query.filter_by(ticket_id=ticket.ticket_id, satisfaction_score=2).count() == 1
