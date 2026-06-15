"""验证各角色助手页示例按钮：①页面渲染出本角色示例；②第一条写操作示例可执行。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
import seed
from ai_prompts import ROLE_EXAMPLE_ACTIONS, ROLE_EXAMPLE_QUESTIONS

ROLES = [
    ("admin", "admin123"),
    ("manager", "manager123"),
    ("service", "service123"),       # customer_service
    ("finance", "finance123"),
    ("safety", "safety123"),
    ("operation", "operation123"),
    ("employee", "employee123"),
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    db_path = Path(tempfile.mkdtemp(prefix="ex_")) / "ex.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    with app.app_context():
        d = seed.seed_departments(); ct = seed.seed_complaint_types(d); e = seed.seed_employees(d)
        p = seed.seed_passengers(); dr = seed.seed_drivers(); o = seed.seed_orders(p, dr)
        seed.seed_complaints_and_tickets(o, ct, e); db.session.commit(); ensure_supporting_objects()
    client = app.test_client()

    print("=" * 90)
    print("各角色助手页示例按钮验证")
    print("=" * 90)
    summary = []
    for username, pwd in ROLES:
        with app.app_context():
            from models import Employee
            emp = Employee.query.filter_by(username=username).first()
            role = emp.role
            expect_q = ROLE_EXAMPLE_QUESTIONS.get(role, [])
            expect_a = ROLE_EXAMPLE_ACTIONS.get(role, [])
        client.post("/login", data={"username": username, "password": pwd})
        page = client.get("/assistant").get_data(as_text=True)
        # ① 页面是否渲染出本角色的示例按钮
        q_rendered = expect_q and all(q in page for q in expect_q)
        a_rendered = expect_a and all(a in page for a in expect_a)
        # ② 第一条写操作示例是否可执行
        first_action = expect_a[0] if expect_a else None
        exec_ok = None
        if first_action:
            data = client.post("/api/assistant/chat", json={"message": first_action}).get_json() or {}
            exec_ok = bool(data.get("ok"))
            if not exec_ok:
                exec_ok = f"FAIL:{data.get('error_code')}"
        verdict = "PASS" if (q_rendered and a_rendered and exec_ok is True) else "FAIL"
        summary.append((role, verdict))
        print(f"\n[{role}] 用户={username}")
        print(f"    页面渲染 查询示例={q_rendered} 写操作示例={a_rendered}")
        print(f"    首条写操作『{first_action}』执行={exec_ok}")
        if verdict == "FAIL" and not exec_ok is True:
            print(f"    -> 写操作未成功，检查 scope/权限/目标")

    print("\n" + "=" * 90)
    print(f"{'角色':18} {'verdict':7}")
    for role, verdict in summary:
        print(f"{role:18} {verdict:7}")
    passed = sum(1 for _, v in summary if v == "PASS")
    print(f"\nPASS {passed}/{len(ROLES)}")


if __name__ == "__main__":
    main()
