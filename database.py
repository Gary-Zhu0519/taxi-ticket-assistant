from __future__ import annotations

import os
import sqlite3
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import quote_plus

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "taxi_complaint.db"
DOCS_DIR = BASE_DIR / "docs"
VIEWS_SQL_PATH = DOCS_DIR / "views.sql"
VIEWS_MYSQL_SQL_PATH = DOCS_DIR / "views_mysql.sql"
INDEXES_SQL_PATH = DOCS_DIR / "indexes.sql"
INDEXES_MYSQL_SQL_PATH = DOCS_DIR / "indexes_mysql.sql"

MYSQL_HOST = os.getenv("MYSQL_HOST", "")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "")
MYSQL_USERNAME = os.getenv("MYSQL_USERNAME", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE_URI = (
    f"mysql+pymysql://{MYSQL_USERNAME}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    if MYSQL_HOST else ""
)
SQLITE_DATABASE_URI = f"sqlite:///{DATABASE_PATH.as_posix()}"

VIEW_NAMES = [
    "v_customer_service_ticket",
    "v_finance_complaint_ticket",
    "v_safety_ticket",
    "v_operation_ticket",
    "v_manager_ticket_summary",
    "v_employee_pending_ticket",
    "v_feedback_result",
]

INDEX_DEFINITIONS = [
    ("ride_order", "idx_order_passenger", "passenger_id"),
    ("ride_order", "idx_order_driver", "driver_id"),
    ("complaint", "idx_complaint_order", "order_id"),
    ("complaint", "idx_complaint_type", "complaint_type_id"),
    ("ticket", "idx_ticket_complaint", "complaint_id"),
    ("ticket", "idx_ticket_status", "ticket_status"),
    ("ticket", "idx_ticket_owner", "current_owner_id"),
    ("ticket", "idx_ticket_department", "department_id"),
    ("assignment_record", "idx_assignment_ticket", "ticket_id"),
    ("escalation_record", "idx_escalation_ticket", "ticket_id"),
    ("action_log", "idx_action_ticket", "ticket_id"),
    ("feedback", "idx_feedback_ticket", "ticket_id"),
]

db = SQLAlchemy()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# SQL 调试开关：默认关闭（不在助手对话里展示）。
# 需要时可用环境变量 SQL_DEBUG_ENABLED=true 打开。
SQL_DEBUG_ENABLED = _env_flag("SQL_DEBUG_ENABLED", default=False)
_sql_capture_enabled: ContextVar[bool] = ContextVar("sql_capture_enabled", default=False)
_sql_capture_statements: ContextVar[list] = ContextVar("sql_capture_statements", default=[])


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


@event.listens_for(Engine, "before_cursor_execute")
def collect_sql_debug_trace(conn, cursor, statement, parameters, context, executemany):
    if not SQL_DEBUG_ENABLED or not _sql_capture_enabled.get():
        return

    statements = list(_sql_capture_statements.get())
    statements.append(
        {
            "statement": str(statement).strip(),
            "parameters": _serialize_sql_parameters(parameters),
            "executemany": bool(executemany),
        }
    )
    _sql_capture_statements.set(statements)


def _serialize_sql_parameters(parameters):
    if parameters is None:
        return None
    if isinstance(parameters, (str, int, float, bool)):
        return parameters
    if isinstance(parameters, dict):
        return {str(key): _serialize_sql_parameters(value) for key, value in parameters.items()}
    if isinstance(parameters, (list, tuple)):
        return [_serialize_sql_parameters(item) for item in parameters]
    return str(parameters)


def begin_sql_debug_capture():
    token_enabled = _sql_capture_enabled.set(True)
    token_statements = _sql_capture_statements.set([])
    return token_enabled, token_statements


def end_sql_debug_capture(tokens=None):
    statements = list(_sql_capture_statements.get())
    if tokens:
        token_enabled, token_statements = tokens
        _sql_capture_enabled.reset(token_enabled)
        _sql_capture_statements.reset(token_statements)
    else:
        _sql_capture_enabled.set(False)
        _sql_capture_statements.set([])
    return statements


def get_default_database_uri() -> str:
    """优先 MySQL（如果环境变量配了），否则用本地 SQLite。"""
    if MYSQL_HOST and MYSQL_DATABASE:
        return MYSQL_DATABASE_URI
    return SQLITE_DATABASE_URI


def is_sqlite_engine(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def is_mysql_engine(engine: Engine) -> bool:
    return engine.dialect.name.startswith("mysql")


def init_app(app):
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", get_default_database_uri())
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {"pool_pre_ping": True, "pool_recycle": 300})
    db.init_app(app)


def _split_sql_statements(sql_text: str) -> list[str]:
    statements = []
    for item in sql_text.split(";"):
        statement = item.strip()
        if statement:
            statements.append(statement)
    return statements


def run_sql_script(sql_path: Path):
    if not sql_path.exists():
        return

    sql_text = sql_path.read_text(encoding="utf-8")
    if not sql_text.strip():
        return

    if is_sqlite_engine(db.engine):
        raw_connection = db.engine.raw_connection()
        try:
            cursor = raw_connection.cursor()
            cursor.executescript(sql_text)
            raw_connection.commit()
        finally:
            raw_connection.close()
        return

    with db.engine.begin() as connection:
        for statement in _split_sql_statements(sql_text):
            connection.execute(text(statement))


def _ensure_mysql_indexes():
    inspector = inspect(db.engine)
    existing_by_table = {
        table_name: {item["name"] for item in inspector.get_indexes(table_name)}
        for table_name, _, _ in INDEX_DEFINITIONS
    }
    with db.engine.begin() as connection:
        for table_name, index_name, column_name in INDEX_DEFINITIONS:
            if index_name in existing_by_table.get(table_name, set()):
                continue
            connection.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({column_name})"))


def ensure_indexes():
    if is_sqlite_engine(db.engine):
        run_sql_script(INDEXES_SQL_PATH)
        return
    if is_mysql_engine(db.engine):
        _ensure_mysql_indexes()
        return
    run_sql_script(INDEXES_SQL_PATH)


def ensure_views():
    if is_mysql_engine(db.engine) and VIEWS_MYSQL_SQL_PATH.exists():
        run_sql_script(VIEWS_MYSQL_SQL_PATH)
        return
    run_sql_script(VIEWS_SQL_PATH)


def ensure_supporting_objects():
    ensure_indexes()
    ensure_views()


def drop_supporting_objects():
    with db.engine.begin() as connection:
        for view_name in VIEW_NAMES:
            connection.execute(text(f"DROP VIEW IF EXISTS {view_name}"))


def describe_current_database() -> str:
    if is_mysql_engine(db.engine):
        return f"MySQL {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
    return f"SQLite {DATABASE_PATH}"
