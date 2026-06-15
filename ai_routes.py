from __future__ import annotations

from flask import Blueprint, g, jsonify, render_template, request

from ai_permissions import build_user_context
from ai_prompts import ROLE_EXAMPLE_ACTIONS, ROLE_EXAMPLE_QUESTIONS
from ai_service import (
    get_ai_runtime_status,
    handle_ai_message_with_debug,
)
from auth import login_required

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/assistant")
@login_required
def assistant_page():
    context = build_user_context(g.current_user)
    unified_workflow = [
        {
            "id": "u1",
            "title": "Human Input",
            "desc": "用户在同一个对话框中输入自然语言需求，可以是查询，也可以是受控业务写操作。",
        },
        {
            "id": "u2",
            "title": "Session Context Builder",
            "desc": "Flask 后端从 session / current_user 构造角色、员工、部门和权限范围，不信任前端传来的身份参数。",
        },
        {
            "id": "u3",
            "title": "Operation Type Classifier",
            "desc": "后端先判断本轮对话属于查询还是受控写操作，并把 operation_type 写入响应结果，供前端展示。",
        },
        {
            "id": "u4",
            "title": "PromptTemplate + Structured Parser",
            "desc": "LangChain 根据 operation_type 选择查询或写操作 Prompt，让 DeepSeek 输出结构化 intent / filters 或 action / payload。",
        },
        {
            "id": "u5",
            "title": "Permission Guard + Tool Router",
            "desc": "后端统一做角色权限、业务边界和参数校验，再把请求路由到查询工具或受控业务服务。",
        },
        {
            "id": "u6",
            "title": "SQLAlchemy / Service Execution",
            "desc": "查询走 SQLAlchemy 多表联查，写操作走受控服务函数；模型永远不直接写 SQL，也不直接改表。",
        },
        {
            "id": "u7",
            "title": "Masking + Summary Chain",
            "desc": "结果先经过脱敏和风险识别，再生成统一回答，并显式展示 operation_type、命令步骤和结构化结果。",
        },
    ]
    return render_template(
        "assistant.html",
        ai_status=get_ai_runtime_status(),
        assistant_context=context,
        example_questions=ROLE_EXAMPLE_QUESTIONS.get(g.current_user.role, []),
        example_actions=ROLE_EXAMPLE_ACTIONS.get(g.current_user.role, []),
        unified_workflow=unified_workflow,
    )


@ai_bp.route("/api/assistant/chat", methods=["POST"])
def assistant_chat_api():
    if g.get("current_user") is None:
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": "UNAUTHORIZED",
                    "message": "请先登录后再使用智能增删改查助手。",
                }
            ),
            401,
        )

    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    result = handle_ai_message_with_debug(message, g.current_user)
    status_code = 200 if result.get("ok") else 400
    if result.get("error_code") in {"UNAUTHORIZED", "PERMISSION_DENIED"}:
        status_code = 403 if result["error_code"] == "PERMISSION_DENIED" else 401
    return jsonify(result), status_code


@ai_bp.route("/api/assistant/action", methods=["POST"])
def assistant_action_api():
    if g.get("current_user") is None:
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": "UNAUTHORIZED",
                    "message": "请先登录后再使用智能增删改查助手。",
                }
            ),
            401,
        )

    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    result = handle_ai_message_with_debug(message, g.current_user)
    status_code = 200 if result.get("ok") else 400
    if result.get("error_code") in {"UNAUTHORIZED", "PERMISSION_DENIED"}:
        status_code = 403 if result["error_code"] == "PERMISSION_DENIED" else 401
    return jsonify(result), status_code
