from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import db, ensure_supporting_objects
from models import RideOrder, Ticket
import seed


def login_as(client, username: str, password: str, follow_redirects: bool = True):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=follow_redirects,
    )


def extract_ticket_ids(html: str) -> list[str]:
    return sorted(set(re.findall(r"TCK[A-Z0-9]{10}", html)))


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    database_dir = tmp_path_factory.mktemp("smoke_db")
    database_path = database_dir / "taxi_smoke.db"
    flask_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        }
    )
    with flask_app.app_context():
        departments = seed.seed_departments()
        complaint_types = seed.seed_complaint_types(departments)
        employees = seed.seed_employees(departments)
        passengers = seed.seed_passengers()
        drivers = seed.seed_drivers()
        orders = seed.seed_orders(passengers, drivers)
        seed.seed_complaints_and_tickets(orders, complaint_types, employees)
        db.session.commit()
        ensure_supporting_objects()

    flask_app.config["SMOKE_DATABASE_PATH"] = database_path
    yield flask_app
    with flask_app.app_context():
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def app_ctx(app):
    with app.app_context():
        yield


@pytest.fixture()
def sample_ids(app):
    with app.app_context():
        order = RideOrder.query.order_by(RideOrder.order_time.desc()).first()
        ticket = Ticket.query.order_by(Ticket.create_time.desc()).first()
        pending_feedback_ticket = Ticket.query.filter_by(ticket_status="待反馈").order_by(Ticket.create_time.desc()).first()
        return {
            "order_id": order.order_id if order else None,
            "ticket_id": ticket.ticket_id if ticket else None,
            "pending_feedback_ticket_id": pending_feedback_ticket.ticket_id if pending_feedback_ticket else None,
        }


@pytest.fixture()
def database_path(app):
    return Path(app.config["SMOKE_DATABASE_PATH"])
