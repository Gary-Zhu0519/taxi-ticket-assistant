from __future__ import annotations

import os
import re

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency is covered by requirements
    OpenAI = None

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - dependency is covered by requirements
    ChatOpenAI = None

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_PLACEHOLDER = "PASTE_YOUR_DEEPSEEK_API_KEY_HERE"


def sanitize_error_message(message: str | Exception) -> str:
    text = str(message or "未知错误")
    if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != DEEPSEEK_PLACEHOLDER:
        text = text.replace(DEEPSEEK_API_KEY, "***")
    text = re.sub(r"sk-[A-Za-z0-9]+", "sk-***", text)
    return text


def is_ai_available() -> bool:
    return bool(DEEPSEEK_API_KEY) and DEEPSEEK_API_KEY != DEEPSEEK_PLACEHOLDER


def get_deepseek_client():
    if not is_ai_available() or OpenAI is None:
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def get_deepseek_llm(temperature: float = 0.0):
    if not is_ai_available() or ChatOpenAI is None:
        return None
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
    )


def call_deepseek_chat(messages, temperature: float = 0.2) -> dict:
    client = get_deepseek_client()
    if client is None:
        return {
            "ok": False,
            "message": "DeepSeek API Key 仍为占位符，智能助手暂不可用。",
        }

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
        )
        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""
        return {
            "ok": True,
            "content": content.strip(),
        }
    except Exception as exc:  # pragma: no cover - network and vendor behavior
        return {
            "ok": False,
            "message": f"DeepSeek 调用失败：{sanitize_error_message(exc)}",
        }
