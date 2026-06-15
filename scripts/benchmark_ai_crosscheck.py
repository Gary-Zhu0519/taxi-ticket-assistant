from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from ai_llm import is_ai_available
from ai_service import handle_ai_chat
from models import Complaint, ComplaintType, Employee, Feedback, Ticket


def safe_text(value) -> str:
    return str(value).encode("unicode_escape").decode("ascii")


def recent_window():
    today = date.today()
    return today - timedelta(days=7), today + timedelta(days=1)


def expected_department_overdue_count(department_name: str) -> int:
    return (
        Ticket.query.filter(
            Ticket.department.has(department_name=department_name),
            Ticket.ticket_status != "已关闭",
            Ticket.sla_deadline < datetime.now(),
        ).count()
    )


def expected_open_fee_dispute_count() -> int:
    return (
        Ticket.query.join(Complaint, Ticket.complaint_id == Complaint.complaint_id)
        .join(ComplaintType, Complaint.complaint_type_id == ComplaintType.complaint_type_id)
        .filter(
            ComplaintType.type_name == "费用争议",
            Ticket.ticket_status != "已关闭",
        )
        .count()
    )


def expected_open_p1_count() -> int:
    return Ticket.query.filter(
        Ticket.priority_level == "P1",
        Ticket.ticket_status != "已关闭",
    ).count()


def expected_recent_low_feedback_count() -> int:
    start_dt, end_dt = recent_window()
    return (
        Feedback.query.filter(
            Feedback.satisfaction_score < 3,
            Feedback.feedback_time >= start_dt,
            Feedback.feedback_time < end_dt,
        ).count()
    )


def expected_employee_todo_count(employee: Employee) -> int:
    return Ticket.query.filter(
        Ticket.current_owner_id == employee.employee_id,
        Ticket.ticket_status != "已关闭",
    ).count()


def expected_department_pending_feedback_count(department_name: str) -> int:
    return Ticket.query.filter(
        Ticket.department.has(department_name=department_name),
        Ticket.ticket_status == "待反馈",
    ).count()


def benchmark_cases():
    return [
        {
            "name": "admin finance overdue count",
            "username": "admin",
            "question": "财务部有多少超时工单",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") == "dashboard_summary"
                and result.get("rows")
                and result["rows"][0]["department_name"] == "财务售后部"
                and result["rows"][0]["overdue_tickets"] == expected_department_overdue_count("财务售后部")
            ),
        },
        {
            "name": "admin customer service overdue count",
            "username": "admin",
            "question": "客服有多少超时工单",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") == "dashboard_summary"
                and result.get("rows")
                and result["rows"][0]["department_name"] == "客服部"
                and result["rows"][0]["overdue_tickets"] == expected_department_overdue_count("客服部")
            ),
        },
        {
            "name": "admin open fee dispute tickets",
            "username": "admin",
            "question": "查询费用争议类未关闭工单",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") in {"ticket_query", "risk_query"}
                and result.get("data_count") == expected_open_fee_dispute_count()
                and all(row["complaint_type"] == "费用争议" for row in result.get("rows", []))
                and all(row["ticket_status"] != "已关闭" for row in result.get("rows", []))
            ),
        },
        {
            "name": "admin open P1 tickets",
            "username": "admin",
            "question": "列出所有P1未关闭工单",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") in {"ticket_query", "risk_query"}
                and result.get("data_count") == expected_open_p1_count()
                and all(row["priority_level"] == "P1" for row in result.get("rows", []))
                and all(row["ticket_status"] != "已关闭" for row in result.get("rows", []))
            ),
        },
        {
            "name": "admin recent low feedback",
            "username": "admin",
            "question": "最近低满意度反馈有哪些",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") == "feedback_query"
                and result.get("data_count") == expected_recent_low_feedback_count()
            ),
        },
        {
            "name": "safety safety events",
            "username": "safety",
            "question": "查询所有安全事件工单",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") in {"ticket_query", "risk_query"}
                and all(row["complaint_type"] == "安全事件" for row in result.get("rows", []))
            ),
        },
        {
            "name": "employee own todo only",
            "username": "employee",
            "question": "我的待处理工单有哪些",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") == "ticket_query"
                and result.get("data_count") == expected_employee_todo_count(user)
                and all(row["current_owner"] == user.employee_name for row in result.get("rows", []))
            ),
        },
        {
            "name": "finance own department pending feedback count",
            "username": "finance",
            "question": "财务售后部待反馈工单有多少",
            "validator": lambda result, user: (
                result.get("ok") is True
                and result.get("intent") == "dashboard_summary"
                and result.get("rows")
                and result["rows"][0]["department_name"] == "财务售后部"
                and result["rows"][0]["pending_feedback_tickets"]
                == expected_department_pending_feedback_count("财务售后部")
            ),
        },
        {
            "name": "employee cross-user denied",
            "username": "employee",
            "question": "查询所有员工的待办工单",
            "validator": lambda result, user: (
                result.get("ok") is False and result.get("error_code") == "PERMISSION_DENIED"
            ),
        },
        {
            "name": "finance dashboard denied",
            "username": "finance",
            "question": "安全部有多少超时工单",
            "validator": lambda result, user: (
                result.get("ok") is False and result.get("error_code") == "PERMISSION_DENIED"
            ),
        },
    ]


def main():
    app = create_app({"TESTING": True})
    with app.app_context():
        if not is_ai_available():
            print("DeepSeek is not available. Skip live AI benchmark.")
            return 1

        total = 0
        passed = 0

        for case in benchmark_cases():
            total += 1
            user = Employee.query.filter_by(username=case["username"]).first()
            result = handle_ai_chat(case["question"], user)
            ok = False
            try:
                ok = case["validator"](result, user)
            except Exception:
                ok = False

            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {case['name']}")
            print(f"  user={case['username']} question={case['question']}")
            print(
                "  result:",
                {
                    "ok": result.get("ok"),
                    "error_code": result.get("error_code"),
                    "intent": result.get("intent"),
                    "data_count": result.get("data_count"),
                },
            )
            if result.get("rows"):
                print("  row0:", result["rows"][0])
            print("  answer:", safe_text((result.get("answer") or result.get("message") or "")[:200]))
            print()
            if ok:
                passed += 1

        print(f"Benchmark summary: {passed}/{total} passed.")
        return 0 if passed == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
