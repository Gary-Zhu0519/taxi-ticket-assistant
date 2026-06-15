"""多跳关系查询回归测试。

走真实的 /api/assistant/chat 全链路（DeepSeek 意图识别 + normalize_filters 启发式 + SQLAlchemy 联查），
用种子数据，逐条打印：最终 intent、系统实际使用的 filters、命中条数、真实 SQL 条数、结果列、结论。

用法:
    python scripts/test_multihop_queries.py            # 真实 DeepSeek
    set RUN_LIVE_AI_TESTS=true && python ...           # 同上（显式）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
import seed

QUERIES = [
    "查询订单 ORD001 对应的投诉记录，并进一步返回这些投诉生成的工单当前负责人以及所属部门信息",
    "查询所有由员工 E003 曾经处理过的工单，并返回每个工单当前的负责人以及其角色类型",
    "查询投诉 C005 对应的工单，并返回该工单所有历史分派记录（包含分派人、接收人、时间）",
    "查询工单 T010 的完整生命周期信息，包括创建信息、分派记录、升级记录、处理日志、反馈记录",
    "查询运营部门中所有优先级为 P1 且未关闭，并且 SLA 剩余时间小于 24 小时的工单列表",
    "查询订单 ORD002 产生的投诉类型，并返回该投诉类型对应的默认责任部门和 SLA 配置",
    "查询员工 E002 作为分派人、日志记录人、升级发起人参与过的所有工单列表，并区分其角色行为类型",
    "查询所有投诉状态为已生成工单的记录，并返回对应工单当前状态是否为关闭或未关闭",
    "查询所有没有分派记录但已有处理日志的工单，并返回对应投诉信息",
    "查询所有反馈评分为 1 或 2 的工单，并返回对应投诉内容、订单信息、司机信息、当前工单负责人",
]

# 每条查询期望回答里必须出现的关键列（用于判定“是否真的多跳取到数据”）
EXPECT_KEYS = {
    0: {"order_id", "ticket_id", "current_owner", "department_name"},
    1: {"ticket_id", "current_owner"},
    2: {"ticket_id"},  # 分派记录历史
    3: {"ticket_id"},  # 生命周期事件
    4: {"department_name", "priority_level", "ticket_status", "ticket_id"},
    5: {"complaint_type", "default_department_name", "default_sla_hours"},
    6: {"ticket_id"},  # 跨角色行为
    7: {"complaint_id", "ticket_status"},
    8: {"ticket_id"},  # 异常路径
    9: {"ticket_id", "complaint_content"},  # 反馈低分深查
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
    if not rows:
        return set()
    return set(rows[0].keys())


def main():
    import tempfile

    # Windows 控制台默认 GBK，DeepSeek 回答里可能含特殊字符，强制 UTF-8 输出避免打印崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    db_dir = tempfile.mkdtemp(prefix="multihop_")
    db_path = Path(db_dir) / "multihop.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
        }
    )
    _seed(app)

    client = app.test_client()
    login = client.post("/login", data={"username": "admin", "password": "admin123"})
    print(f"[login] status={login.status_code}")

    print("=" * 100)
    print("多跳关系查询回归基线（admin 角色，真实 DeepSeek）")
    print("=" * 100)

    summary = []
    for idx, message in enumerate(QUERIES):
        resp = client.post("/api/assistant/chat", json={"message": message})
        data = resp.get_json() or {}
        ok = data.get("ok")
        intent = data.get("intent")
        op_type = data.get("operation_type")
        commands = data.get("commands") or []
        steps = data.get("executed_steps") or []
        tool = commands[0].get("tool") if commands else "-"
        used_filters = _filters_of(commands)
        data_count = (steps[0].get("data_count") if steps else None) or data.get("data_count")
        sql_count = len(data.get("sql_debug") or [])
        risk_flags = data.get("risk_flags") or []
        rows = data.get("rows") or []
        keys = _row_keys(rows)
        answer = (data.get("answer") or "").replace("\n", " ")[:160]

        expect = EXPECT_KEYS.get(idx, set())
        # 判定：成功且命中数据且结果列覆盖期望关键列
        projection_ok = expect.issubset(keys) if (expect and rows) else (bool(rows) if not expect else True)
        verdict = "PASS" if (ok and data_count and projection_ok) else ("NODATA" if ok and not data_count else "FAIL")

        summary.append((idx + 1, verdict, intent, tool, data_count, sql_count))
        print(f"\n[{idx + 1}/10] {message}")
        print(f"    -> ok={ok} verdict={verdict} op={op_type} intent={intent} tool={tool}")
        print(f"    -> 实际 filters: {json.dumps(used_filters, ensure_ascii=False)}")
        print(f"    -> data_count={data_count}  sql_debug={sql_count}  risk_flags={risk_flags}")
        print(f"    -> 结果列: {sorted(keys)}")
        if expect:
            print(f"    -> 期望列: {sorted(expect)}  覆盖={projection_ok}")
        print(f"    -> answer: {answer}")
        if not ok:
            print(f"    -> error: {data.get('error_code')} {data.get('message')}")

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    print(f"{'#':>2}  {'verdict':7} {'intent':16} {'tool':22} {'count':>5} {'sql':>3}")
    for num, verdict, intent, tool, data_count, sql_count in summary:
        print(f"{num:>2}  {verdict:7} {str(intent):16} {str(tool):22} {str(data_count):>5} {sql_count:>3}")
    passed = sum(1 for _, v, *_ in summary if v == "PASS")
    print(f"\nPASS {passed}/10")


if __name__ == "__main__":
    main()
