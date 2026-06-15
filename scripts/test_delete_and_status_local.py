"""本地 SQLite 验证 delete_ticket + 分类器/状态映射修复（不碰线上库）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
from models import ActionLog, Ticket
import seed


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    db_path = Path(tempfile.mkdtemp(prefix="dt_")) / "dt.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    with app.app_context():
        d = seed.seed_departments(); ct = seed.seed_complaint_types(d); e = seed.seed_employees(d)
        p = seed.seed_passengers(); dr = seed.seed_drivers(); o = seed.seed_orders(p, dr)
        seed.seed_complaints_and_tickets(o, ct, e); db.session.commit(); ensure_supporting_objects()
        t1 = Ticket.query.order_by(Ticket.create_time.asc()).first().ticket_id
        t2 = Ticket.query.order_by(Ticket.create_time.asc()).offset(1).first().ticket_id
        t3 = Ticket.query.order_by(Ticket.create_time.asc()).offset(2).first().ticket_id

    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})

    cases = [
        f"删除工单 {t1} 这个",
        f"修改工单 {t2} 的状态为已关闭",
        f"删除工单 {t3} 的处理日志",
    ]
    for msg in cases:
        r = client.post("/api/assistant/chat", json={"message": msg}).get_json() or {}
        op = r.get("operation_type")
        intent = r.get("intent")
        ok = r.get("ok")
        err = r.get("error_code")
        print(f"[{op}] intent={intent} ok={ok} err={err} | {msg}")

    with app.app_context():
        print(f"{t1} 删除后仍存在? {bool(Ticket.query.get(t1))}")
        print(f"{t2} 关闭后状态? {Ticket.query.get(t2).ticket_status if Ticket.query.get(t2) else '-'}")
        print(f"{t3} 日志数? {ActionLog.query.filter_by(ticket_id=t3).count()}")


if __name__ == "__main__":
    main()
