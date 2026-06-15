# DeepSeek API 集成说明

## 1. 接入方式

项目通过 `ai_llm.py` 以 OpenAI-compatible 方式接入 DeepSeek：

- `OpenAI(api_key=..., base_url="https://api.deepseek.com")`
- LangChain 使用 `ChatOpenAI` 复用同一配置

## 2. 硬编码配置位置

文件：

```text
ai_llm.py
```

核心常量：

```python
DEEPSEEK_API_KEY = "PASTE_YOUR_DEEPSEEK_API_KEY_HERE"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
```

## 3. base_url

```text
https://api.deepseek.com
```

## 4. model

```text
deepseek-v4-flash
```

## 5. 安全注意事项

1. 真实项目不建议硬编码 API Key。
2. 本课程项目按本地演示需求保留后端硬编码占位符。
3. API Key 不应出现在模板、JS、日志、README 或报错信息中。
4. 本项目对错误信息做了脱敏处理，避免把 Key 回传给前端。

## 6. 连接测试脚本

运行：

```bash
python scripts/check_deepseek_connection.py
```

行为：

1. 如果仍是占位符，提示先替换。
2. 如果已替换，发送“请回复 pong”。
3. 成功则输出 `DeepSeek API connected successfully`。

## 7. 常见错误排查

### 7.1 显示“DeepSeek API Key 仍为占位符”

说明尚未把 `DEEPSEEK_API_KEY` 替换成真实 Key。

### 7.2 `/api/assistant/chat` 返回 `AI_NOT_CONFIGURED`

说明当前仍是占位符，或者依赖未正确安装。

### 7.3 真实连通性测试失败

请检查：

1. Key 是否已替换
2. 网络是否可访问 `https://api.deepseek.com`
3. 模型名是否按课程要求填写
4. 当前环境是否安装了 `openai` 和 `langchain-openai`
