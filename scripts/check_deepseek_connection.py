from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_llm import DEEPSEEK_API_KEY, DEEPSEEK_PLACEHOLDER, call_deepseek_chat, is_ai_available


def main():
    if not is_ai_available() or DEEPSEEK_API_KEY == DEEPSEEK_PLACEHOLDER:
        print("请先替换 DEEPSEEK_API_KEY")
        return

    result = call_deepseek_chat(
        [{"role": "user", "content": "请回复 pong"}],
        temperature=0,
    )
    if result["ok"]:
        print("DeepSeek API connected successfully")
        print(result["content"])
    else:
        print(result["message"])


if __name__ == "__main__":
    main()
