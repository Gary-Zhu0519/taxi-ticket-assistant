from __future__ import annotations

from datetime import datetime

from database import db

ROLE_CHOICES = [
    "admin",
    "customer_service",
    "finance",
    "safety",
    "operation",
    "manager",
    "employee",
]

ROLE_LABELS = {
    "admin": "系统管理员",
    "customer_service": "客服人员",
    "finance": "财务售后",
    "safety": "安全人员",
    "operation": "运营人员",
    "manager": "部门主管",
    "employee": "普通员工",
}

ORDER_STATUS_CHOICES = ["已完成", "已取消", "进行中", "异常"]
PRIORITY_CHOICES = ["P1", "P2", "P3", "P4"]
URGENCY_CHOICES = ["U1", "U2", "U3", "U4"]
COMPLAINT_STATUS_CHOICES = ["已受理", "处理中", "已关闭"]
TICKET_STATUS_CHOICES = ["待分派", "处理中", "已升级", "待反馈", "已关闭", "已重开"]
EMPLOYEE_STATUS_CHOICES = ["在职", "离职"]


class Passenger(db.Model):
    __tablename__ = "passenger"

    passenger_id = db.Column(db.String(32), primary_key=True)
    passenger_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    account_status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    orders = db.relationship("RideOrder", back_populates="passenger")
    complaints = db.relationship("Complaint", back_populates="passenger")
    feedbacks = db.relationship("Feedback", back_populates="passenger")


class Driver(db.Model):
    __tablename__ = "driver"

    driver_id = db.Column(db.String(32), primary_key=True)
    driver_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    driver_score = db.Column(db.Float, nullable=False)
    driver_status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    orders = db.relationship("RideOrder", back_populates="driver")


class RideOrder(db.Model):
    __tablename__ = "ride_order"
    __table_args__ = (
        db.CheckConstraint(
            "order_status IN ('已完成', '已取消', '进行中', '异常')",
            name="ck_ride_order_status",
        ),
    )

    order_id = db.Column(db.String(32), primary_key=True)
    passenger_id = db.Column(
        db.String(32),
        db.ForeignKey("passenger.passenger_id"),
        nullable=False,
    )
    driver_id = db.Column(
        db.String(32),
        db.ForeignKey("driver.driver_id"),
        nullable=False,
    )
    start_location = db.Column(db.String(100), nullable=False)
    end_location = db.Column(db.String(100), nullable=False)
    order_time = db.Column(db.DateTime, nullable=False)
    finish_time = db.Column(db.DateTime)
    order_amount = db.Column(db.Numeric(10, 2), nullable=False)
    order_status = db.Column(db.String(20), nullable=False)

    passenger = db.relationship("Passenger", back_populates="orders")
    driver = db.relationship("Driver", back_populates="orders")
    complaints = db.relationship("Complaint", back_populates="order")


class Department(db.Model):
    __tablename__ = "department"

    department_id = db.Column(db.String(32), primary_key=True)
    department_name = db.Column(db.String(50), nullable=False, unique=True)
    department_type = db.Column(db.String(50), nullable=False)
    manager_id = db.Column(
        db.String(32),
        db.ForeignKey("employee.employee_id", use_alter=True, name="fk_department_manager"),
    )

    employees = db.relationship(
        "Employee",
        back_populates="department",
        foreign_keys="Employee.department_id",
    )
    manager = db.relationship("Employee", foreign_keys=[manager_id], post_update=True)
    complaint_types = db.relationship("ComplaintType", back_populates="default_department")
    tickets = db.relationship("Ticket", back_populates="department")


class ComplaintType(db.Model):
    __tablename__ = "complaint_type"
    __table_args__ = (
        db.CheckConstraint(
            "default_priority_level IN ('P1', 'P2', 'P3', 'P4')",
            name="ck_complaint_type_priority",
        ),
    )

    complaint_type_id = db.Column(db.String(32), primary_key=True)
    type_name = db.Column(db.String(50), nullable=False, unique=True)
    type_description = db.Column(db.Text, nullable=False)
    default_department_id = db.Column(
        db.String(32),
        db.ForeignKey("department.department_id"),
        nullable=False,
    )
    default_priority_level = db.Column(db.String(2), nullable=False)
    default_sla_hours = db.Column(db.Integer, nullable=False)

    default_department = db.relationship("Department", back_populates="complaint_types")
    complaints = db.relationship("Complaint", back_populates="complaint_type")


class Employee(db.Model):
    __tablename__ = "employee"
    __table_args__ = (
        db.UniqueConstraint("username", name="uq_employee_username"),
        db.CheckConstraint(
            "role IN ('admin', 'customer_service', 'finance', 'safety', 'operation', 'manager', 'employee')",
            name="ck_employee_role",
        ),
        db.CheckConstraint(
            "employee_status IN ('在职', '离职')",
            name="ck_employee_status",
        ),
    )

    employee_id = db.Column(db.String(32), primary_key=True)
    employee_name = db.Column(db.String(50), nullable=False)
    department_id = db.Column(
        db.String(32),
        db.ForeignKey("department.department_id"),
        nullable=False,
    )
    role = db.Column(db.String(30), nullable=False)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    employee_status = db.Column(db.String(20), nullable=False)

    department = db.relationship(
        "Department",
        back_populates="employees",
        foreign_keys=[department_id],
    )
    owned_tickets = db.relationship("Ticket", back_populates="current_owner", foreign_keys="Ticket.current_owner_id")
    assignments_made = db.relationship(
        "AssignmentRecord",
        back_populates="assigner",
        foreign_keys="AssignmentRecord.assigner_id",
    )
    assignments_received = db.relationship(
        "AssignmentRecord",
        back_populates="receiver",
        foreign_keys="AssignmentRecord.receiver_id",
    )
    escalations = db.relationship(
        "EscalationRecord",
        back_populates="escalated_by_employee",
        foreign_keys="EscalationRecord.escalated_by",
    )
    action_logs = db.relationship("ActionLog", back_populates="employee")

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)


class Complaint(db.Model):
    __tablename__ = "complaint"
    __table_args__ = (
        db.CheckConstraint(
            "urgency_level IN ('U1', 'U2', 'U3', 'U4')",
            name="ck_complaint_urgency",
        ),
        db.CheckConstraint(
            "complaint_status IN ('已受理', '处理中', '已关闭')",
            name="ck_complaint_status",
        ),
    )

    complaint_id = db.Column(db.String(32), primary_key=True)
    order_id = db.Column(db.String(32), db.ForeignKey("ride_order.order_id"), nullable=False)
    passenger_id = db.Column(db.String(32), db.ForeignKey("passenger.passenger_id"), nullable=False)
    complaint_type_id = db.Column(
        db.String(32),
        db.ForeignKey("complaint_type.complaint_type_id"),
        nullable=False,
    )
    complaint_content = db.Column(db.Text, nullable=False)
    complaint_time = db.Column(db.DateTime, nullable=False, default=datetime.now)
    urgency_level = db.Column(db.String(2), nullable=False)
    complaint_status = db.Column(db.String(20), nullable=False, default="已受理")

    order = db.relationship("RideOrder", back_populates="complaints")
    passenger = db.relationship("Passenger", back_populates="complaints")
    complaint_type = db.relationship("ComplaintType", back_populates="complaints")
    ticket = db.relationship("Ticket", back_populates="complaint", uselist=False)


class Ticket(db.Model):
    __tablename__ = "ticket"
    __table_args__ = (
        db.UniqueConstraint("complaint_id", name="uq_ticket_complaint"),
        db.CheckConstraint(
            "priority_level IN ('P1', 'P2', 'P3', 'P4')",
            name="ck_ticket_priority",
        ),
        db.CheckConstraint(
            "ticket_status IN ('待分派', '处理中', '已升级', '待反馈', '已关闭', '已重开')",
            name="ck_ticket_status",
        ),
    )

    ticket_id = db.Column(db.String(32), primary_key=True)
    complaint_id = db.Column(
        db.String(32),
        db.ForeignKey("complaint.complaint_id"),
        nullable=False,
    )
    priority_level = db.Column(db.String(2), nullable=False)
    ticket_status = db.Column(db.String(20), nullable=False, default="待分派")
    department_id = db.Column(
        db.String(32),
        db.ForeignKey("department.department_id"),
        nullable=False,
    )
    current_owner_id = db.Column(db.String(32), db.ForeignKey("employee.employee_id"))
    create_time = db.Column(db.DateTime, nullable=False, default=datetime.now)
    sla_deadline = db.Column(db.DateTime, nullable=False)
    close_time = db.Column(db.DateTime)

    complaint = db.relationship("Complaint", back_populates="ticket")
    department = db.relationship("Department", back_populates="tickets", foreign_keys=[department_id])
    current_owner = db.relationship("Employee", back_populates="owned_tickets", foreign_keys=[current_owner_id])
    assignments = db.relationship("AssignmentRecord", back_populates="ticket", order_by="desc(AssignmentRecord.assign_time)")
    escalations = db.relationship("EscalationRecord", back_populates="ticket", order_by="desc(EscalationRecord.escalation_time)")
    action_logs = db.relationship("ActionLog", back_populates="ticket", order_by="desc(ActionLog.action_time)")
    feedback = db.relationship("Feedback", back_populates="ticket", uselist=False)


class AssignmentRecord(db.Model):
    __tablename__ = "assignment_record"

    assignment_id = db.Column(db.String(32), primary_key=True)
    ticket_id = db.Column(db.String(32), db.ForeignKey("ticket.ticket_id"), nullable=False)
    assigner_id = db.Column(db.String(32), db.ForeignKey("employee.employee_id"), nullable=False)
    receiver_id = db.Column(db.String(32), db.ForeignKey("employee.employee_id"), nullable=False)
    department_id = db.Column(db.String(32), db.ForeignKey("department.department_id"), nullable=False)
    assign_time = db.Column(db.DateTime, nullable=False, default=datetime.now)
    assignment_note = db.Column(db.Text)

    ticket = db.relationship("Ticket", back_populates="assignments")
    assigner = db.relationship("Employee", back_populates="assignments_made", foreign_keys=[assigner_id])
    receiver = db.relationship("Employee", back_populates="assignments_received", foreign_keys=[receiver_id])
    department = db.relationship("Department")


class EscalationRecord(db.Model):
    __tablename__ = "escalation_record"

    escalation_id = db.Column(db.String(32), primary_key=True)
    ticket_id = db.Column(db.String(32), db.ForeignKey("ticket.ticket_id"), nullable=False)
    from_level = db.Column(db.String(50), nullable=False)
    to_level = db.Column(db.String(50), nullable=False)
    escalation_reason = db.Column(db.Text, nullable=False)
    escalated_by = db.Column(db.String(32), db.ForeignKey("employee.employee_id"), nullable=False)
    escalation_time = db.Column(db.DateTime, nullable=False, default=datetime.now)

    ticket = db.relationship("Ticket", back_populates="escalations")
    escalated_by_employee = db.relationship(
        "Employee",
        back_populates="escalations",
        foreign_keys=[escalated_by],
    )


class ActionLog(db.Model):
    __tablename__ = "action_log"

    log_id = db.Column(db.String(32), primary_key=True)
    ticket_id = db.Column(db.String(32), db.ForeignKey("ticket.ticket_id"), nullable=False)
    employee_id = db.Column(db.String(32), db.ForeignKey("employee.employee_id"), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    action_content = db.Column(db.Text, nullable=False)
    action_time = db.Column(db.DateTime, nullable=False, default=datetime.now)

    ticket = db.relationship("Ticket", back_populates="action_logs")
    employee = db.relationship("Employee", back_populates="action_logs")


class Feedback(db.Model):
    __tablename__ = "feedback"
    __table_args__ = (
        db.UniqueConstraint("ticket_id", name="uq_feedback_ticket"),
        db.CheckConstraint(
            "satisfaction_score >= 1 AND satisfaction_score <= 5",
            name="ck_feedback_score",
        ),
    )

    feedback_id = db.Column(db.String(32), primary_key=True)
    ticket_id = db.Column(
        db.String(32),
        db.ForeignKey("ticket.ticket_id"),
        nullable=False,
        unique=True,
    )
    passenger_id = db.Column(db.String(32), db.ForeignKey("passenger.passenger_id"), nullable=False)
    satisfaction_score = db.Column(db.Integer, nullable=False)
    feedback_content = db.Column(db.Text, nullable=False)
    feedback_time = db.Column(db.DateTime, nullable=False, default=datetime.now)

    ticket = db.relationship("Ticket", back_populates="feedback")
    passenger = db.relationship("Passenger", back_populates="feedbacks")
