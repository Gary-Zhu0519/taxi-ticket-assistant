from __future__ import annotations

import sqlite3

from models import (
    ActionLog,
    AssignmentRecord,
    Complaint,
    ComplaintType,
    Department,
    Driver,
    Employee,
    EscalationRecord,
    Feedback,
    Passenger,
    RideOrder,
    Ticket,
)

EXPECTED_TABLES = {
    "passenger",
    "driver",
    "ride_order",
    "complaint_type",
    "complaint",
    "department",
    "employee",
    "ticket",
    "assignment_record",
    "escalation_record",
    "action_log",
    "feedback",
}

EXPECTED_INDEXES = {
    "idx_order_passenger",
    "idx_order_driver",
    "idx_complaint_order",
    "idx_complaint_type",
    "idx_ticket_complaint",
    "idx_ticket_status",
    "idx_ticket_owner",
    "idx_ticket_department",
    "idx_assignment_ticket",
    "idx_escalation_ticket",
    "idx_action_ticket",
    "idx_feedback_ticket",
}


def test_database_file_created(database_path):
    assert database_path.exists()


def test_core_tables_exist(database_path):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    table_names = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)


def test_seed_data_counts(app_ctx):
    assert Passenger.query.count() == 10
    assert Driver.query.count() == 8
    assert RideOrder.query.count() == 20
    assert ComplaintType.query.count() == 7
    assert Department.query.count() == 6
    assert Employee.query.count() == 12
    assert Complaint.query.count() == 20
    assert Ticket.query.count() == 20
    assert AssignmentRecord.query.count() >= 20
    assert EscalationRecord.query.count() >= 1
    assert ActionLog.query.count() >= 20
    assert Feedback.query.count() >= 1


def test_core_indexes_exist(database_path):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    index_names = {row[0] for row in rows}
    assert EXPECTED_INDEXES.issubset(index_names)


def test_foreign_key_relationships_work(app_ctx):
    ticket = Ticket.query.first()
    assert ticket is not None
    assert ticket.complaint is not None
    assert ticket.department is not None

    complaint = Complaint.query.first()
    assert complaint.order is not None
    assert complaint.passenger is not None
    assert complaint.complaint_type is not None

    order = RideOrder.query.first()
    assert order.passenger is not None
    assert order.driver is not None
