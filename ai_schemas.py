from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str | None = None
    complaint_id: str | None = None
    order_id: str | None = None
    passenger_id: str | None = None
    driver_id: str | None = None
    employee_id: str | None = None
    ticket_status: str | None = None
    complaint_status: str | None = None
    order_status: str | None = None
    priority_level: str | None = None
    complaint_type: str | None = None
    department_name: str | None = None
    employee_name: str | None = None
    urgency_level: str | None = None
    is_overdue: bool | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    score_below: int | None = None
    to_level: str | None = None
    query_kind: str | None = None
    date_start: str | None = None
    date_end: str | None = None
    # 关系存在性 / SLA 剩余时间等通用过滤维度，供多跳关系查询使用（非分析类）
    has_assignment: bool | None = None
    has_action_log: bool | None = None
    has_escalation: bool | None = None
    has_feedback: bool | None = None
    sla_within_hours: int | None = None
    limit: int = Field(default=10)

    @field_validator(
        "ticket_id",
        "complaint_id",
        "order_id",
        "passenger_id",
        "driver_id",
        "employee_id",
        "ticket_status",
        "complaint_status",
        "order_status",
        "priority_level",
        "complaint_type",
        "department_name",
        "employee_name",
        "urgency_level",
        "to_level",
        "query_kind",
        "date_start",
        "date_end",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("ticket_id", "complaint_id", "order_id", "passenger_id", "driver_id", "employee_id", mode="after")
    @classmethod
    def uppercase_ids(cls, value):
        return value.upper() if value else value

    @field_validator("min_amount", "max_amount", mode="before")
    @classmethod
    def normalize_amount(cls, value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @field_validator("score_below", mode="before")
    @classmethod
    def normalize_score_below(cls, value):
        if value in (None, ""):
            return None
        try:
            score = int(value)
        except (TypeError, ValueError):
            return None
        return min(max(score, 1), 5)

    @field_validator("sla_within_hours", mode="before")
    @classmethod
    def normalize_sla_within_hours(cls, value):
        if value in (None, ""):
            return None
        try:
            hours = int(value)
        except (TypeError, ValueError):
            return None
        return min(max(hours, 1), 720)

    @field_validator("limit", mode="before")
    @classmethod
    def clamp_limit(cls, value):
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return 10
        return min(max(limit, 1), 50)


class OperationTypeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation_type: Literal["query", "write"]
    reason: str = ""


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal[
        "ticket_query",
        "ticket_detail",
        "complaint_query",
        "order_query",
        "dashboard_summary",
        "feedback_query",
        "risk_query",
        "action_suggestion",
        "unsupported",
        "permission_sensitive",
    ]
    filters: QueryFilters = Field(default_factory=QueryFilters)
    reason: str = ""


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal[
        "ticket_query",
        "ticket_detail",
        "complaint_query",
        "order_query",
        "dashboard_summary",
        "feedback_query",
        "risk_query",
        "action_suggestion",
        "unsupported",
        "permission_sensitive",
    ]
    filters: QueryFilters = Field(default_factory=QueryFilters)
    step_title: str = ""
    depends_on_previous: bool = False
    reference_field: Literal["none", "department_name", "complaint_type", "ticket_id", "order_id"] = "none"
    reason: str = ""


class QueryPlanResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[PlanStep] = Field(default_factory=list)
    reason: str = ""


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str | None = None
    complaint_id: str | None = None
    order_id: str | None = None
    employee_id: str | None = None
    complaint_type: str | None = None
    urgency_level: str | None = None
    priority_level: str | None = None
    order_status: str | None = None
    complaint_content: str | None = None
    receiver_id: str | None = None
    receiver_name: str | None = None
    assignment_note: str | None = None
    action_type: str | None = None
    action_content: str | None = None
    from_level: str | None = None
    to_level: str | None = None
    escalation_reason: str | None = None
    satisfaction_score: int | None = None
    feedback_content: str | None = None
    log_id: str | None = None
    delete_reason: str | None = None
    close_reason: str | None = None

    @field_validator(
        "ticket_id",
        "complaint_id",
        "order_id",
        "employee_id",
        "complaint_type",
        "urgency_level",
        "priority_level",
        "order_status",
        "complaint_content",
        "receiver_id",
        "receiver_name",
        "assignment_note",
        "action_type",
        "action_content",
        "from_level",
        "to_level",
        "escalation_reason",
        "feedback_content",
        "log_id",
        "delete_reason",
        "close_reason",
        mode="before",
    )
    @classmethod
    def normalize_action_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("ticket_id", "complaint_id", "order_id", "employee_id", "receiver_id", "log_id", mode="after")
    @classmethod
    def uppercase_action_ids(cls, value):
        return value.upper() if value else value

    @field_validator("satisfaction_score", mode="before")
    @classmethod
    def normalize_score(cls, value):
        if value in (None, ""):
            return None
        try:
            score = int(value)
        except (TypeError, ValueError):
            return None
        return min(max(score, 1), 5)


class ActionIntentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal[
        "create_complaint",
        "update_order_status",
        "delete_order",
        "update_complaint_urgency",
        "delete_complaint",
        "delete_ticket",
        "create_ticket_for_complaint",
        "assign_ticket",
        "add_action_log",
        "escalate_ticket",
        "update_ticket_priority",
        "close_ticket",
        "set_pending_feedback",
        "submit_feedback",
        "reopen_ticket",
        "delete_action_log",
        "revoke_assignment",
        "unsupported",
        "permission_sensitive",
    ]
    payload: ActionPayload = Field(default_factory=ActionPayload)
    reason: str = ""
