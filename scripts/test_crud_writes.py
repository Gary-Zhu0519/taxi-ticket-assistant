"""10 条简单增删改查（受控写操作）执行校验。走真实 /api/assistant/chat 全链路，每条断言 ok+动作+数据库实际变化。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
from models import (
    ActionLog, AssignmentRecord, Complaint, EscalationRecord, Feedback, RideOrder, Ticket,
)
import seed


def _nth_ticket_id(n: int) -> str:
    return (
        Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc())
        .offset(n - 1)
        .first()
        .ticket_id
    )


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    db_path = Path(tempfile.mkdtemp(prefix="crud_")) / "crud.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    with app.app_context():
        d = seed.seed_departments()
        ct = seed.seed_complaint_types(d)
        e = seed.seed_employees(d)
        p = seed.seed_passengers()
        dr = seed.seed_drivers()
        o = seed.seed_orders(p, dr)
        seed.seed_complaints_and_tickets(o, ct, e)
        db.session.commit()
        ensure_supporting_objects()

    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})

    # (序号, 期望动作, 语句, 校验函数)  校验函数在 app_ctx 内执行，返回 (通过, 详情)
    cases = [
        (
            "create_complaint",
            "基于 ORD003 创建一条费用争议投诉，紧急程度 U2，内容是乘客认为多收费",
            lambda: (Complaint.query.count() == c_before + 1 and Ticket.query.count() == t_before + 1, f"投诉{c_before}->{Complaint.query.count()} 工单{t_before}->{Ticket.query.count()}"),
        ),
        (
            "assign_ticket",
            "把工单 T002 分派给员工 E004，备注优先处理",
            lambda: (Ticket.query.get(_nth_ticket_id(2)).current_owner_id == "EMP004", f"current_owner={Ticket.query.get(_nth_ticket_id(2)).current_owner_id}"),
        ),
        (
            "add_action_log",
            "给工单 T003 新增一条处理日志，动作类型为联系乘客，内容是已回访乘客确认情况",
            lambda: (ActionLog.query.filter_by(ticket_id=_nth_ticket_id(3)).count() == logs_t3_before + 1, f"日志{logs_t3_before}->{ActionLog.query.filter_by(ticket_id=_nth_ticket_id(3)).count()}"),
        ),
        (
            "escalate_ticket",
            "将工单 T008 升级到主管复核，原因是涉及责任划分不清",
            lambda: (Ticket.query.get(_nth_ticket_id(8)).ticket_status == "已升级" and EscalationRecord.query.filter_by(ticket_id=_nth_ticket_id(8)).count() >= 1, f"状态={Ticket.query.get(_nth_ticket_id(8)).ticket_status}"),
        ),
        (
            "update_ticket_priority",
            "修改工单 T006 的优先级为 P1",
            lambda: (Ticket.query.get(_nth_ticket_id(6)).priority_level == "P1", f"优先级={Ticket.query.get(_nth_ticket_id(6)).priority_level}"),
        ),
        (
            "set_pending_feedback",
            "将工单 T011 设为待反馈",
            lambda: (Ticket.query.get(_nth_ticket_id(11)).ticket_status == "待反馈", f"状态={Ticket.query.get(_nth_ticket_id(11)).ticket_status}"),
        ),
        (
            "submit_feedback",
            "提交工单 T005 的反馈，评分 5 分，内容是乘客认可处理结果",
            lambda: ((fb := Feedback.query.filter_by(ticket_id=_nth_ticket_id(5)).first()) and fb.satisfaction_score == 5, f"评分={fb.satisfaction_score if fb else '无'}"),
        ),
        (
            "close_ticket",
            "关闭工单 T016，原因是问题已解决",
            lambda: (Ticket.query.get(_nth_ticket_id(16)).ticket_status == "已关闭" and Ticket.query.get(_nth_ticket_id(16)).close_time is not None, f"状态={Ticket.query.get(_nth_ticket_id(16)).ticket_status}"),
        ),
        (
            "reopen_ticket",
            "重开工单 T004，原因是乘客再次反馈问题",
            lambda: (Ticket.query.get(_nth_ticket_id(4)).ticket_status == "已重开", f"状态={Ticket.query.get(_nth_ticket_id(4)).ticket_status}"),
        ),
        (
            "update_order_status",
            "更新订单 ORD005 的状态为已完成",
            lambda: (RideOrder.query.get("ORD005").order_status == "已完成", f"订单状态={RideOrder.query.get('ORD005').order_status}"),
        ),
    ]

    print("=" * 100)
    print("10 条简单增删改查执行校验（admin，真实 DeepSeek）")
    print("=" * 100)

    # 预取基线计数（供 create/add_log 校验闭包引用）
    with app.app_context():
        globals()["c_before"] = Complaint.query.count()
        globals()["t_before"] = Ticket.query.count()
        globals()["logs_t3_before"] = ActionLog.query.filter_by(ticket_id=_nth_ticket_id(3)).count()

    summary = []
    for idx, (expect_action, message, verify) in enumerate(cases, 1):
        # 执行前快照（写操作会改 DB，部分校验依赖 before 闭包已预取）
        resp = client.post("/api/assistant/chat", json={"message": message})
        data = resp.get_json() or {}
        ok = data.get("ok")
        intent = data.get("intent")
        op = data.get("operation_type")
        rows = data.get("rows") or []
        err = data.get("error_code")

        with app.app_context():
            try:
                v_ok, v_detail = verify()
            except Exception as exc:
                v_ok, v_detail = False, f"校验异常: {exc}"

        action_match = intent == expect_action
        verdict = "PASS" if (ok and action_match and v_ok) else "FAIL"
        summary.append((idx, verdict, intent, ok, v_ok))
        print(f"\n[{idx}/10] {message}")
        print(f"    -> ok={ok} op={op} intent={intent} (期望 {expect_action}) {'✓动作' if action_match else '✗动作'}")
        print(f"    -> 执行校验: {'✓' if v_ok else '✗'} {v_detail}")
        if not ok:
            print(f"    -> error: {err} {data.get('message')}")

    print("\n" + "=" * 100)
    print(f"{'#':>2}  {'verdict':7} {'intent':26} {'ok':>5} {'dbEffect':>8}")
    for num, verdict, intent, ok, v_ok in summary:
        print(f"{num:>2}  {verdict:7} {str(intent):26} {str(ok):>5} {str(v_ok):>8}")
    passed = sum(1 for _, v, *_ in summary if v == "PASS")
    print(f"\nPASS {passed}/10")


if __name__ == "__main__":
    main()
