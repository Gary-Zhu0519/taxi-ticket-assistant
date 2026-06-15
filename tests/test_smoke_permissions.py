from __future__ import annotations

from models import Complaint, ComplaintType, Department, Employee, Ticket
from tests.conftest import extract_ticket_ids, login_as


def test_unauthenticated_access_redirects_to_login(client):
    response = client.get("/tickets")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_default_accounts_can_login(app):
    accounts = [
        ("admin", "admin123"),
        ("manager", "manager123"),
        ("service", "service123"),
        ("finance", "finance123"),
        ("safety", "safety123"),
        ("operation", "operation123"),
        ("employee", "employee123"),
    ]
    for username, password in accounts:
        client = app.test_client()
        response = login_as(client, username, password)
        assert response.status_code == 200, username


def test_admin_and_manager_can_access_dashboard(app):
    for username, password in [("admin", "admin123"), ("manager", "manager123")]:
        client = app.test_client()
        login_as(client, username, password)
        response = client.get("/dashboard")
        assert response.status_code == 200, username


def test_finance_scope_filters_non_finance_tickets(app, app_ctx):
    client = app.test_client()
    login_as(client, "finance", "finance123")
    response = client.get("/tickets")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    visible_ids = extract_ticket_ids(html)
    assert visible_ids
    visible_types = (
        Ticket.query.join(Complaint)
        .join(ComplaintType)
        .join(Department, Ticket.department_id == Department.department_id)
        .with_entities(ComplaintType.type_name, Department.department_name)
        .filter(Ticket.ticket_id.in_(visible_ids))
        .all()
    )
    assert all(type_name != "安全事件" for type_name, _ in visible_types)
    assert all(
        department_name == "财务售后部" or type_name == "费用争议"
        for type_name, department_name in visible_types
    )


def test_safety_can_see_p1_or_safety_tickets(app):
    client = app.test_client()
    login_as(client, "safety", "safety123")
    response = client.get("/tickets")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "P1" in html or "安全事件" in html


def test_employee_only_sees_owned_open_tickets(app, app_ctx):
    client = app.test_client()
    login_as(client, "employee", "employee123")
    response = client.get("/tickets")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    employee = Employee.query.filter_by(username="employee").first()
    expected_ids = {
        ticket.ticket_id
        for ticket in Ticket.query.filter(
            Ticket.current_owner_id == employee.employee_id,
            Ticket.ticket_status != "已关闭",
        ).all()
    }
    visible_ids = set(extract_ticket_ids(html))

    assert visible_ids <= expected_ids
    assert all(ticket_id in html for ticket_id in expected_ids)
