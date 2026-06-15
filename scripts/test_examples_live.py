"""真跑各角色前端示例（真实 MySQL），跑完整表回填还原。安全：先存 JSON 备份，单事务回填，最后逐表比对。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from app import create_app
from database import db, describe_current_database
from models import ActionLog, AssignmentRecord, Complaint, EscalationRecord, Feedback, Ticket
from ai_prompts import ROLE_EXAMPLE_ACTIONS, ROLE_EXAMPLE_QUESTIONS

TABLES = [Ticket, Complaint, ActionLog, AssignmentRecord, EscalationRecord, Feedback]
ROLES = [
    ("admin", "admin123", "admin"), ("manager", "manager123", "manager"),
    ("service", "service123", "customer_service"), ("finance", "finance123", "finance"),
    ("safety", "safety123", "safety"), ("operation", "operation123", "operation"),
    ("employee", "employee123", "employee"),
]
BACKUP_FILE = Path(__file__).resolve().parent / "examples_live_backup.json"


def _val(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def snapshot():
    return {m.__name__: [{c.name: _val(getattr(o, c.name)) for c in m.__table__.columns} for o in m.query.all()] for m in TABLES}


def restore(snap):
    """整表回填：关 FK 校验，删后按快照重插，单事务。"""
    conn = db.engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for m in TABLES:
            cur.execute(f"DELETE FROM {m.__tablename__}")
        for m in TABLES:  # FK 已关，顺序无所谓
            cols = [c.name for c in m.__table__.columns]
            ph = ",".join(["%s"] * len(cols))
            sql = f"INSERT INTO {m.__tablename__} ({','.join(cols)}) VALUES ({ph})"
            for row in snap[m.__name__]:
                cur.execute(sql, [row[c] for c in cols])
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    finally:
        conn.close()
    db.session.remove()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    app = create_app()
    client = app.test_client()
    with app.app_context():
        print("数据库:", describe_current_database())
        snap = snapshot()
        BACKUP_FILE.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"已备份 {sum(len(v) for v in snap.values())} 行到 {BACKUP_FILE.name}")

    print("=" * 92)
    print("真跑各角色前端示例（跑完整表还原）")
    print("=" * 92)
    summary = []
    try:
        for username, pwd, role in ROLES:
            client.post("/login", data={"username": username, "password": pwd})
            for kind, items in (("查", ROLE_EXAMPLE_QUESTIONS.get(role, [])), ("写", ROLE_EXAMPLE_ACTIONS.get(role, []))):
                for msg in items:
                    r = client.post("/api/assistant/chat", json={"message": msg}).get_json() or {}
                    ok = r.get("ok"); intent = r.get("intent"); err = r.get("error_code")
                    summary.append((role, kind, bool(ok), intent, err, msg))
                    print(f"[{role:16}{kind}] ok={ok} intent={intent} {err or ''} | {msg}")
            # 每个角色跑完即还原
            with app.app_context():
                restore(snap)
    finally:
        with app.app_context():
            restore(snap)  # 兜底再还原一次

    with app.app_context():
        cur = snapshot()
        ok = cur == snap
        print("\n" + "=" * 92)
        print(f"示例 {len(summary)} 条 | 写操作执行 {sum(1 for s in summary if s[1]=='写' and s[2])}/{sum(1 for s in summary if s[1]=='写')}")
        print(f"最终整表还原校验：{'通过 ✅（DB 与原始完全一致）' if ok else '失败 ❌（见备份文件 examples_live_backup.json）'}")


if __name__ == "__main__":
    main()
