# LangChain + DeepSeek 智能查询助手设计说明

## 1. 集成目标

本模块的目标不是做通用聊天机器人，而是做一个“权限内自然语言查询助手”：

1. 识别用户问题的业务意图。
2. 抽取结构化过滤条件。
3. 通过后端受控工具函数查询数据。
4. 总结查询结果并返回风险提示。

## 2. 为什么不用 Coze iframe

本项目是数据库课程设计后台系统，更适合展示：

- Flask session 权限隔离
- SQLAlchemy 只读工具查询
- 大模型只负责理解，不直接触库

iframe 方式不利于说明后端内部的数据权限链路，因此本项目采用原生页面集成。

## 3. 为什么不用通用 SQL Agent

本项目明确不让模型直接生成并执行任意 SQL，原因如下：

1. 容易越权访问全库。
2. 难以保证只读。
3. 不利于和 Flask 现有权限体系对齐。
4. 课程答辩更适合展示“意图识别 + 受控工具函数”的可解释架构。

## 4. DeepSeek 集成方式

项目通过 `ai_llm.py` 接入 DeepSeek OpenAI-compatible API：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL = "https://api.deepseek.com"`
- `DEEPSEEK_MODEL = "deepseek-v4-flash"`

LangChain 侧通过 `langchain_openai.ChatOpenAI` 复用同一配置。

## 5. 查询流程

`POST /api/assistant/chat` 的处理流程：

1. 从 `session / g.current_user` 获取真实用户。
2. 构建 `UserContext`。
3. 检测是否为写操作请求。
4. 检查 DeepSeek 是否可用。
5. 用 LangChain / DeepSeek 做意图识别与参数抽取。
6. 执行后端权限校验。
7. 调用 `ai_tools.py` 中的只读查询函数。
8. 识别 `risk_flags`。
9. 用 DeepSeek 总结结构化结果。
10. 返回 JSON 给前端。

## 6. 权限隔离设计

权限上下文不从前端读取，只从服务器 session 获取。

角色范围：

- `admin`：全量数据
- `manager`：全量工单、反馈、统计
- `customer_service`：客服部、待分派、普通受理范围
- `finance`：财务售后部、费用争议相关范围
- `safety`：安全事件、安全部、P1 范围
- `operation`：运营部、司机服务、取消争议范围
- `employee`：本人负责的未关闭工单

## 7. 只读工具函数

`ai_tools.py` 提供：

- `query_tickets`
- `get_ticket_detail`
- `query_complaints`
- `query_orders`
- `query_dashboard_summary`
- `query_feedback`
- `query_risk_tickets`
- `suggest_ticket_action`

这些工具都遵循：

1. 只读
2. 先过权限过滤
3. 默认 10 条，最多 50 条
4. 不执行写操作

## 8. 脱敏设计

1. 普通员工手机号只显示前三后四。
2. 非安全角色查看安全事件内容时只返回摘要。
3. `finance` 不返回安全事件细节。
4. `operation` 不返回财务处理细节。
5. 所有角色都不会拿到密码字段。
6. 所有错误信息都会对可能的 Key 片段做脱敏。

## 9. 前端页面设计

`/assistant` 页面展示：

- 当前用户
- 当前角色
- 当前部门
- 权限范围说明
- 角色示例问题
- 聊天气泡
- 风险提示
- 结果表格

## 10. 演示建议

1. 用 `admin` 展示全量查询。
2. 用 `employee` 展示本人待办查询。
3. 演示“查询所有员工工单”被拒绝。
4. 演示“帮我关闭工单”只返回只读提示。
