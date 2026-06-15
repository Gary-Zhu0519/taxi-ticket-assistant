from __future__ import annotations

from sqlalchemy import text

from database import db
from tests.conftest import login_as

VIEW_NAMES = [
    "v_customer_service_ticket",
    "v_finance_complaint_ticket",
    "v_safety_ticket",
    "v_operation_ticket",
    "v_manager_ticket_summary",
    "v_employee_pending_ticket",
    "v_feedback_result",
]


def test_sql_views_are_queryable(app_ctx):
    for view_name in VIEW_NAMES:
        result = db.session.execute(text(f"SELECT * FROM {view_name} LIMIT 5"))
        rows = result.fetchall()
        assert len(result.keys()) > 0, view_name
        assert rows is not None, view_name


def test_list_pages_render_tables_or_empty_states(client):
    login_as(client, "admin", "admin123")
    page_keywords = {
        "/orders": "订单",
        "/complaints": "投诉",
        "/tickets": "工单",
        "/dashboard": "统计",
        "/schema": "数据库",
            "/assistant": "智能增删改查助手",
    }

    for path, keyword in page_keywords.items():
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert keyword in html, path
        assert "<table" in html or "暂无" in html or "聊天" in html or "对话" in html or "Workflow" in html, path
