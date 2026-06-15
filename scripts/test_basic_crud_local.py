"""本地 SQLite 跑通基础小范围增删改查（每个动作类型一条），证明 CRUD 链路可用。不碰线上库。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
from models import ActionLog, Complaint, Ticket
import seed


def _nth(n):
    return Ticket.query.order_by(Ticket.create_time.asc(), Ticket.ticket_id.asc()).offset(n - 1).first().ticket_id


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    db_path = Path(tempfile.mkdtemp(prefix="crud_")) / "crud.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    with app.app_context():
        d = seed.seed_departments(); ct = seed.seed_complaint_types(d); e = seed.seed_employees(d)
        p = seed.seed_passengers(); dr = seed.seed_drivers(); o = seed.seed_orders(p, dr)
        seed.seed_complaints_and_tickets(o, ct, e); db.session.commit(); ensure_supporting_objects()
        t2, t3, t6, t16, t20 = _nth(2), _nth(3), _nth(6), _nth(16), _nth(20)
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})

    cases = [
        ("增·创建投诉", "基于 ORD002 创建一条物品遗失投诉，紧急程度 U3，内容是乘客遗失背包"),
        ("改·分派工单", f"把 {t2} 分派给 E004，备注优先处理"),
        ("改·改优先级", f"修改 {t6} 的优先级为 P1"),
        ("增·加处理日志", f"给 {t3} 新增处理日志，动作类型为联系乘客，内容是已联系乘客"),
        ("删·删处理日志", f"删除 {t2} 最新的一条处理日志"),
        ("改·关闭工单", f"关闭 {t16}，原因是问题已解决"),
        ("删·删工单", f"删除 {t20} 这个工单"),
    ]
    print("=" * 88)
    print("基础小范围 增删改查 本地跑通验证（admin）")
    print("=" * 88)
    for label, msg in cases:
        r = client.post("/api/assistant/chat", json={"message": msg}).get_json() or {}
        print(f"[{r.get('operation_type')}] {label}: intent={r.get('intent')} ok={r.get('ok')} err={r.get('error_code')}")
    with app.app_context():
        print(f"\n校验：投诉数={Complaint.query.count()} 工单数={Ticket.query.count()} (创建+1/删工单-1 抵消应为20)")


if __name__ == "__main__":
    main()
