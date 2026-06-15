from __future__ import annotations

from tests.conftest import login_as


def test_application_can_start(app):
    assert app is not None
    assert app.config["TESTING"] is True


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "登录".encode("utf-8") in response.data


def test_admin_can_open_main_pages(client, sample_ids):
    login_response = login_as(client, "admin", "admin123")
    assert login_response.status_code == 200

    page_expectations = {
        "/": "首页",
        "/orders": "订单",
        f"/orders/{sample_ids['order_id']}": "订单",
        "/complaints": "投诉",
        "/complaints/new": "投诉",
        f"/complaints/new?order_id={sample_ids['order_id']}": "投诉",
        "/tickets": "工单",
        f"/tickets/{sample_ids['ticket_id']}": "工单",
        f"/tickets/{sample_ids['ticket_id']}/assign": "分派",
        f"/tickets/{sample_ids['ticket_id']}/escalate": "升级",
        f"/tickets/{sample_ids['ticket_id']}/log": "处理日志",
        f"/tickets/{sample_ids['pending_feedback_ticket_id']}/feedback": "反馈",
        "/dashboard": "统计",
        "/schema": "数据库",
        "/assistant": "智能增删改查助手",
    }

    for path, keyword in page_expectations.items():
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, path
        assert keyword.encode("utf-8") in response.data, path
