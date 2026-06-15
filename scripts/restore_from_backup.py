"""从 examples_live_backup.json 整表回填，把库恢复成测试前状态。幂等。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db
from models import ActionLog, AssignmentRecord, Complaint, EscalationRecord, Feedback, Ticket

TABLES = [Ticket, Complaint, ActionLog, AssignmentRecord, EscalationRecord, Feedback]
BACKUP = Path(__file__).resolve().parent / "examples_live_backup.json"


def _parse(v):
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and "T" in v:
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return v
    return v


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not BACKUP.exists():
        print("找不到备份文件，无法恢复。")
        return
    snap = json.loads(BACKUP.read_text(encoding="utf-8"))
    app = create_app()
    with app.app_context():
        conn = db.engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for m in TABLES:
                cur.execute(f"DELETE FROM {m.__tablename__}")
            for m in TABLES:
                cols = [c.name for c in m.__table__.columns]
                ph = ",".join(["%s"] * len(cols))
                sql = f"INSERT INTO {m.__tablename__} ({','.join(cols)}) VALUES ({ph})"
                for row in snap[m.__name__]:
                    cur.execute(sql, [_parse(row[c]) for c in cols])
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            conn.commit()
        finally:
            conn.close()
        db.session.remove()
        print(f"已从备份回填。当前：工单 {Ticket.query.count()} 投诉 {Complaint.query.count()} "
              f"日志 {ActionLog.query.count()} 分派 {AssignmentRecord.query.count()} 升级 {EscalationRecord.query.count()} 反馈 {Feedback.query.count()}")


if __name__ == "__main__":
    main()
