from __future__ import annotations

import pytest

import ai_llm
import ai_service
from ai_schemas import IntentResult, QueryFilters
from tests.conftest import login_as


class _FakeStructuredLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, messages):
        return self.result


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, schema):
        return _FakeStructuredLLM(self.result)


def _mock_ai_runtime(monkeypatch, intent_result: IntentResult):
    monkeypatch.setattr(
        ai_service,
        "get_ai_runtime_status",
        lambda: {"available": True, "model_name": "deepseek-v4-flash", "message": "mock ready"},
    )
    monkeypatch.setattr(ai_service, "get_deepseek_client", lambda: object())
    monkeypatch.setattr(ai_service, "get_deepseek_llm", lambda temperature=0.0: _FakeLLM(intent_result))
    monkeypatch.setattr(
        ai_service,
        "call_deepseek_chat",
        lambda messages, temperature=0.2: {"ok": True, "content": "这是模拟的智能查询结果。"},
    )


def test_assistant_requires_login(client):
    response = client.get("/assistant")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_assistant_page_renders_after_login(client):
    login_as(client, "admin", "admin123")
    response = client.get("/assistant")
    assert response.status_code == 200
    assert "智能增删改查助手".encode("utf-8") in response.data


def test_assistant_runtime_status_does_not_crash(client):
    login_as(client, "admin", "admin123")
    response = client.post("/api/assistant/chat", json={"message": "我的待处理工单有哪些？"})
    assert response.status_code < 500
    payload = response.get_json()
    if ai_llm.is_ai_available():
        assert payload["ok"] is True
        assert "intent" in payload
    else:
        assert payload["ok"] is False
        assert payload["error_code"] == "AI_NOT_CONFIGURED"


def test_assistant_write_operation_is_rejected_without_db_changes(client):
    login_as(client, "admin", "admin123")
    response = client.post("/api/assistant/chat", json={"message": "帮我删除所有工单。"})
    payload = response.get_json()
    # 批量/越权写操作：被权限层拒绝（不再用只读兜底提示）
    assert payload["ok"] is False
    assert payload["error_code"] == "PERMISSION_DENIED"


def test_assistant_mock_query_returns_structured_json(monkeypatch, client):
    login_as(client, "employee", "employee123")
    _mock_ai_runtime(
        monkeypatch,
        IntentResult(
            intent="ticket_query",
            filters=QueryFilters(priority_level="P1", limit=10),
            reason="这是模拟的智能查询结果。",
        ),
    )

    response = client.post("/api/assistant/chat", json={"message": "我的待处理工单有哪些？"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "ticket_query"
    assert "answer" in payload
    assert "rows" in payload


def test_employee_query_all_staff_tickets_is_denied(monkeypatch, client):
    login_as(client, "employee", "employee123")
    _mock_ai_runtime(
        monkeypatch,
        IntentResult(
            intent="ticket_query",
            filters=QueryFilters(employee_name="全部员工", limit=10),
            reason="模拟越权查询。",
        ),
    )

    response = client.post("/api/assistant/chat", json={"message": "查询所有员工的待办工单。"})
    assert response.status_code == 403
    payload = response.get_json()
    assert payload["error_code"] == "PERMISSION_DENIED"


@pytest.mark.skipif(
    not ai_service.should_run_live_ai_tests() or not ai_llm.is_ai_available(),
    reason="未启用 RUN_LIVE_AI_TESTS=true 或 DeepSeek API 当前不可用。",
)
def test_live_deepseek_query_when_enabled(client):
    login_as(client, "admin", "admin123")
    response = client.post("/api/assistant/chat", json={"message": "列出所有 P1 未关闭工单。"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "answer" in payload
