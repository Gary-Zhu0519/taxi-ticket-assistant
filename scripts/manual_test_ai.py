from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from ai_permissions import build_user_context, check_permission
from ai_schemas import QueryFilters
from ai_service import build_read_only_response, detect_permission_sensitive_request, detect_write_operation_request
from ai_tools import get_ticket_detail, query_dashboard_summary, query_tickets
from models import Complaint, ComplaintType, Employee, Ticket


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    app = create_app()
    with app.app_context():
        admin = Employee.query.filter_by(username="admin").first()
        manager = Employee.query.filter_by(username="manager").first()
        finance = Employee.query.filter_by(username="finance").first()
        safety = Employee.query.filter_by(username="safety").first()
        employee = Employee.query.filter_by(username="employee").first()

        assert_true(all([admin, manager, finance, safety, employee]), "默认测试账号不完整，请先运行 python seed.py。")

        admin_context = build_user_context(admin)
        admin_result = query_tickets(admin_context, QueryFilters(priority_level="P1", ticket_status="未关闭", limit=50))
        assert_true(admin_result["data_count"] >= 1, "admin 应能查询到全量 P1 未关闭工单。")

        manager_context = build_user_context(manager)
        manager_result = query_dashboard_summary(manager_context, QueryFilters(limit=20))
        assert_true(manager_result["data_count"] >= 1, "manager 应能查询部门统计。")

        finance_context = build_user_context(finance)
        finance_permission = check_permission(finance_context, "ticket_detail", QueryFilters(complaint_type="安全事件"))
        assert_true(not finance_permission["allowed"], "finance 查询安全事件详情应被拒绝。")

        safety_context = build_user_context(safety)
        safety_result = query_tickets(safety_context, QueryFilters(priority_level="P1", limit=50))
        assert_true(safety_result["data_count"] >= 1, "safety 应能查询 P1 工单。")

        employee_context = build_user_context(employee)
        employee_result = query_tickets(employee_context, QueryFilters(limit=50))
        assert_true(
            all(row["current_owner"] == employee.employee_name for row in employee_result["rows"]),
            "employee 只能查询当前负责人为自己的工单。",
        )
        assert_true(
            all(row["ticket_status"] != "已关闭" for row in employee_result["rows"]),
            "employee 不能查询已关闭工单。",
        )

        assert_true(
            detect_permission_sensitive_request("查询所有员工工单", employee_context),
            "employee 查询所有员工工单时应命中越权检测。",
        )

        assert_true(
            detect_write_operation_request("帮我关闭 TCK123456 工单"),
            "写操作请求应被识别为只读禁止场景。",
        )
        read_only_response = build_read_only_response()
        assert_true(
            "仅支持查询和处理建议" in read_only_response["answer"],
            "写操作响应应返回只读提示。",
        )

        safety_type = ComplaintType.query.filter_by(type_name="安全事件").first()
        safety_ticket = (
            Ticket.query.join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
            .filter(Complaint.complaint_type_id == safety_type.complaint_type_id)
            .first()
        )
        if safety_ticket:
            masked_detail = get_ticket_detail(finance_context, safety_ticket.ticket_id)
            assert_true(
                masked_detail["data_count"] == 0 or "敏感内容已脱敏" in str(masked_detail["rows"]),
                "finance 查询安全事件详情时应被拒绝或被脱敏。",
            )

        print("manual_test_ai.py: all checks passed.")


if __name__ == "__main__":
    main()
