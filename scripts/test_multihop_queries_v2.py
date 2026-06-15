"""高难度多跳查询测试集 v2（10 条）。走真实 /api/assistant/chat 全链路。"""
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
    "查询工单 T003 对应的投诉内容，并返回该工单的反馈记录以及反馈对应的乘客信息",
    "查询司机 D002 所有被投诉的订单，并返回每条投诉的类型和当前对应工单状态",
    "查询工单 T008 当前所属部门，并返回该部门负责人以及当前工单处理人信息",
    "查询投诉类型为费用争议的所有投诉，并返回其生成工单的优先级、状态以及是否发生过升级",
    "查询工单 T001 和 T002 的所有分派记录，并区分每次分派的发送人、接收人和部门变化",
    "查询员工 E004 参与过的所有工单，并分别列出其在这些工单中担任的角色（分派 / 处理 / 升级）",
    "查询投诉 C007 对应的订单信息，司机信息，并返回最终工单处理状态",
    "查询所有 SLA 已经过半但仍未分派的工单，并返回对应投诉内容和创建时间",
    "查询所有已经存在处理日志但没有分派记录的工单，并返回对应投诉来源",
    "查询工单 T006 的完整链路信息：投诉 → 订单 → 司机 → 分派记录 → 当前负责人 → 最后一次处理日志内容",
]

EXPECT_KEYS = {
    0: {"complaint_content", "feedback_content"},
    1: {"complaint_type", "ticket_status"},
    2: {"department_name", "current_owner"},
    3: {"complaint_type", "priority_level", "ticket_status"},
    4: {"assigner_name", "receiver_name"},
    5: {"participation_roles"},
    6: {"order_id", "driver_name", "ticket_status"},
    7: {"complaint_content"},
    8: {"ticket_id"},
    9: {"complaint_content"},
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

    db_path = Path(tempfile.mkdtemp(prefix="mhv2_")) / "mhv2.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}"})
    _seed(app)
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})

    print("=" * 100)
    print("高难度多跳查询 v2（admin，真实 DeepSeek）")
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
    print(f"{'#':>2}  {'verdict':7} {'intent':16} {'tool':22} {'count':>5}")
    for num, verdict, intent, tool, data_count in summary:
        print(f"{num:>2}  {verdict:7} {str(intent):16} {str(tool):22} {str(data_count):>5}")
    passed = sum(1 for _, v, *_ in summary if v == "PASS")
    print(f"\nPASS {passed}/10")


if __name__ == "__main__":
    main()
