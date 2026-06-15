"""高难度多跳查询测试集 v3（10 条）。走真实 /api/assistant/chat 全链路。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
import seed

QUERIES = [
    "查询投诉 C010 生成的工单，并返回订单信息、投诉内容、工单状态、当前负责人、最近一条处理日志",
    "查询司机 D005 所有被投诉记录，并返回对应订单信息、工单状态以及是否发生过升级",
    "查询工单 T009 中所有参与员工的行为记录，并区分其在分派、处理日志、升级记录中的角色",
    "查询订单 ORD010 是否存在投诉，如果存在，返回投诉类型、工单优先级、当前状态和 SLA 截止时间",
    "查询工单 T002、T004、T006 的完整生命周期信息（分派、升级、日志、反馈）",
    "查询司机服务类投诉对应的所有工单，并返回每个工单的当前负责人、部门和最后操作时间",
    "查询员工 E006 在所有工单中参与的记录，并列出其对应的工单ID、操作类型以及发生时间",
    "查询所有状态为处理中的工单，并返回其最新分派记录以及当前负责人是否一致",
    "查询所有已关闭但仍存在未结束处理日志的工单，并返回对应投诉与订单信息",
    "查询工单 T007 的完整链路：投诉 → 订单 → 司机 → 工单 → 分派记录 → 升级记录 → 处理日志 → 反馈记录",
]

EXPECT_KEYS = {
    0: {"complaint_content", "current_owner", "ticket_status"},
    1: {"driver_name", "ticket_status", "order_id"},
    2: {"actor_name", "event_type"},
    3: {"complaint_type", "priority_level", "ticket_status", "sla_deadline"},
    4: {"event_type", "actor_name"},
    5: {"current_owner", "department_name"},
    6: {"ticket_id", "action_type", "action_time"},
    7: {"ticket_id", "current_owner"},
    8: {"ticket_id"},
    9: {"complaint_content", "driver_name", "current_owner"},
}


def _seed(app):
    with app.app_context():
        departments = seed.seed_departments()
        complaint_types = seed.seed_complaint_types(departments)
        employees = seed.seed_employees(departments)
        passengers = seed.seed_passengers()
        drivers = seed.seed_drivers()
        orders = seed.seed_orders(passengers, drivers)
        seed.seed_complaints_and_tickets(orders, complaint_types, employees)
        db.session.commit()
        ensure_supporting_objects()


def _filters_of(commands):
    if not commands:
        return {}
    f = commands[0].get("filters") or {}
    return {k: v for k, v in f.items() if v not in (None, "", [], {})}


def _row_keys(rows):
    return set(rows[0].keys()) if rows else set()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    db_path = Path(tempfile.mkdtemp(prefix="mhv3_")) / "mhv3.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    _seed(app)
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})

    print("=" * 100)
    print("高难度多跳查询 v3（admin，真实 DeepSeek）")
    print("=" * 100)
    summary = []
    for idx, message in enumerate(QUERIES):
        resp = client.post("/api/assistant/chat", json={"message": message})
        data = resp.get_json() or {}
        ok = data.get("ok")
        intent = data.get("intent")
        op = data.get("operation_type")
        commands = data.get("commands") or []
        steps = data.get("executed_steps") or []
        tool = commands[0].get("tool") if commands else "-"
        data_count = (steps[0].get("data_count") if steps else None) or data.get("data_count")
        rows = data.get("rows") or []
        keys = _row_keys(rows)
        expect = EXPECT_KEYS.get(idx, set())
        projection_ok = expect.issubset(keys) if (expect and rows) else (bool(rows) if not expect else True)
        verdict = "PASS" if (ok and data_count and projection_ok) else ("NODATA" if ok and not data_count else "FAIL")
        summary.append((idx + 1, verdict, intent, tool, data_count))
        print(f"\n[{idx+1}/10] {message}")
        print(f"    -> ok={ok} verdict={verdict} op={op} intent={intent} tool={tool}")
        print(f"    -> filters: {json.dumps(_filters_of(commands), ensure_ascii=False)}")
        print(f"    -> data_count={data_count}  结果列: {sorted(keys)}")
        if expect:
            miss = sorted(expect - keys)
            print(f"    -> 期望列: {sorted(expect)}  缺失={miss if miss else '无'}")
        if not ok:
            print(f"    -> error: {data.get('error_code')} {data.get('message')}")

    print("\n" + "=" * 100)
    print(f"{'#':>2}  {'verdict':7} {'intent':16} {'tool':24} {'count':>5}")
    for num, verdict, intent, tool, data_count in summary:
        print(f"{num:>2}  {verdict:7} {str(intent):16} {str(tool):24} {str(data_count):>5}")
    passed = sum(1 for _, v, *_ in summary if v == "PASS")
    print(f"\nPASS {passed}/10")


if __name__ == "__main__":
    main()
