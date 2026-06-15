# 第三方打车平台后台订单投诉与工单闭环管理系统

基于 `Flask + SQLite + SQLAlchemy + Bootstrap 5 + Jinja2` 实现的数据库课程设计项目，重点展示第三方打车平台后台投诉与工单闭环处理，不实现完整打车前台业务。

## 1. 项目简介

系统围绕以下闭环展开：

1. 订单作为投诉背景。
2. 乘客基于订单提交投诉。
3. 系统自动生成工单。
4. 后台按部门和员工分派工单。
5. 处理过程中记录分派、升级、处理日志和反馈。
6. 主管和管理员查看统计看板。
7. 智能查询助手基于自然语言查询权限范围内的数据。

## 2. 业务背景

本项目不是完整打车平台，不实现：

- 实时派单
- 地图路线规划
- 在线支付
- 司机接单
- 车辆管理

系统只保留轻量订单背景，并重点展示：

- `Complaint`：乘客提出的原始问题
- `Ticket`：后台对投诉的处理任务
- `Complaint_Type`：投诉分类字典和默认规则表
- `Assignment_Record / Escalation_Record / Action_Log`：工单处理过程记录
- `Feedback`：乘客满意度与反馈结果

## 3. 技术栈

- 后端：`Python Flask`
- 数据库：`SQLite`
- ORM：`SQLAlchemy`
- 前端：`Jinja2 + Bootstrap 5 + 原生 JavaScript`
- 大模型接入：`LangChain + DeepSeek(OpenAI-compatible API)`
- 测试：`pytest`

## 4. 项目结构

```text
taxi-db/
├─ app.py
├─ auth.py
├─ database.py
├─ models.py
├─ services.py
├─ seed.py
├─ ai_llm.py
├─ ai_routes.py
├─ ai_service.py
├─ ai_schemas.py
├─ ai_tools.py
├─ ai_permissions.py
├─ ai_prompts.py
├─ requirements.txt
├─ README.md
├─ templates/
├─ static/
├─ docs/
├─ scripts/
└─ tests/
```

## 5. 安装与运行

安装依赖：

```bash
pip install -r requirements.txt
```

初始化数据库：

```bash
python seed.py
```

启动项目：

```bash
python app.py
```

访问地址：

```text
http://127.0.0.1:5000
http://127.0.0.1:5000/assistant
```

## 6. 默认账号

| 角色 | 用户名 | 密码 |
|---|---|---|
| 管理员 | `admin` | `admin123` |
| 主管 | `manager` | `manager123` |
| 客服 | `service` | `service123` |
| 财务 | `finance` | `finance123` |
| 安全 | `safety` | `safety123` |
| 运营 | `operation` | `operation123` |
| 普通员工 | `employee` | `employee123` |

## 7. DeepSeek 硬编码配置

本次课程演示版按要求将 DeepSeek 配置写死在后端 Python 文件中：

- 文件：`ai_llm.py`
- 常量：`DEEPSEEK_API_KEY`
- 常量：`DEEPSEEK_BASE_URL`
- 常量：`DEEPSEEK_MODEL`

当前代码中使用占位符：

```python
DEEPSEEK_API_KEY = "PASTE_YOUR_DEEPSEEK_API_KEY_HERE"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
```

你只需要在本地打开 `ai_llm.py`，把 `DEEPSEEK_API_KEY` 的占位符替换成自己的真实 Key 即可。

重要说明：

- API Key 只存在于后端 Python 文件。
- 前端模板、JS、README、docs、测试输出里都不会显示 API Key。
- 报错信息也会做脱敏处理。

## 8. 为什么真实项目不应硬编码 API Key

真实项目中不建议把 API Key 写死到源码中，因为这会带来：

- 泄漏风险
- 提交仓库风险
- 多环境切换困难
- 权限轮换困难

但本课程项目强调本地演示和快速答辩，因此按要求采用“后端本地硬编码占位符”的方式，方便你本地直接替换后演示。

## 9. 智能查询助手说明

智能助手页面：

```text
/assistant
```

聊天接口：

```text
POST /api/assistant/chat
```

助手遵循以下原则：

1. 当前用户身份只从 Flask session 获取。
2. 前端不能决定 `role / employee_id / department_id`。
3. DeepSeek 只负责意图识别、参数抽取和结果总结。
4. 数据查询只能走后端受控工具函数。
5. 禁止直接生成 SQL 并执行。
6. 禁止执行新增、修改、删除、关闭、分派、升级等写操作。

如果用户要求写操作，系统会返回：

```text
当前智能助手仅支持查询和处理建议，不能直接修改数据库，请进入对应后台页面操作。
```

## 10. 权限说明

- `admin`：可查看和操作所有数据。
- `manager`：可查看所有工单和统计数据。
- `customer_service`：可查看客服部工单、待分派工单，可创建投诉。
- `finance`：只能查看财务售后部和费用争议相关工单。
- `safety`：只能查看安全事件、安全部和 P1 工单。
- `operation`：只能查看运营部、司机服务和取消争议相关工单。
- `employee`：只能查看当前负责人等于自己的未关闭工单。

AI 助手也会复用同一套权限隔离规则。

## 11. 智能查询示例

管理员 / 主管：

- `本周各部门工单数量是多少？`
- `列出所有 P1 未关闭工单。`
- `最近低满意度反馈有哪些？`

客服：

- `查询客服部待分派工单。`
- `今天新增了哪些投诉？`

财务：

- `查询费用争议类未关闭工单。`
- `列出退款相关投诉。`

安全：

- `查询所有安全事件工单。`
- `列出 P1 未关闭工单。`

运营：

- `查询司机服务类投诉。`
- `列出取消争议相关工单。`

普通员工：

- `我的待处理工单有哪些？`
- `我负责的 P1 工单有哪些？`

## 12. 数据库对象

### 12 张核心表

1. `Passenger`
2. `Driver`
3. `Ride_Order`
4. `Complaint_Type`
5. `Complaint`
6. `Department`
7. `Employee`
8. `Ticket`
9. `Assignment_Record`
10. `Escalation_Record`
11. `Action_Log`
12. `Feedback`

### SQL 视图

- `v_customer_service_ticket`
- `v_finance_complaint_ticket`
- `v_safety_ticket`
- `v_operation_ticket`
- `v_manager_ticket_summary`
- `v_employee_pending_ticket`
- `v_feedback_result`

### 核心索引

- `idx_order_passenger`
- `idx_order_driver`
- `idx_complaint_order`
- `idx_complaint_type`
- `idx_ticket_complaint`
- `idx_ticket_status`
- `idx_ticket_owner`
- `idx_ticket_department`
- `idx_assignment_ticket`
- `idx_escalation_ticket`
- `idx_action_ticket`
- `idx_feedback_ticket`

## 13. 烟测运行方法

运行全部 smoke tests：

```bash
python scripts/run_smoke_tests.py
```

单独运行 mock AI 测试：

```bash
python -m pytest tests/test_smoke_ai_assistant.py
```

单独运行数据库视图测试：

```bash
python -m pytest tests/test_smoke_views.py
```

## 14. 真实 DeepSeek 连通性测试

连通性脚本：

```bash
python scripts/check_deepseek_connection.py
```

如果 `DEEPSEEK_API_KEY` 还是占位符，脚本会提示：

```text
请先替换 DEEPSEEK_API_KEY
```

## 15. Mock 测试与真实 AI 测试

默认 smoke tests 不会真实消耗 DeepSeek 配额，测试里会 monkeypatch DeepSeek 调用。

只有在你明确设置：

```powershell
$env:RUN_LIVE_AI_TESTS="true"
```

然后再运行：

```bash
python -m pytest tests/test_smoke_ai_assistant.py
```

才会尝试真实调用 DeepSeek。

## 16. 完整演示流程

推荐答辩演示顺序：

1. 用 `admin / admin123` 登录。
2. 进入订单详情页，基于订单创建投诉。
3. 系统自动生成工单。
4. 进入工单详情，分派给员工。
5. 新增处理日志。
6. 升级工单。
7. 设为待反馈。
8. 提交反馈，观察高分自动关闭、低分自动重开。
9. 打开 `/dashboard` 展示统计。
10. 打开 `/schema` 展示数据库设计。
11. 打开 `/assistant` 展示智能查询助手。
12. 切换到 `employee / employee123`，演示“我的待办”和“查询所有员工工单”的权限差异。

## 17. 相关文档

- `docs/database_design.md`
- `docs/er_description.md`
- `docs/sql_examples.sql`
- `docs/views.sql`
- `docs/indexes.sql`
- `docs/langchain_design.md`
- `docs/ai_query_examples.md`
- `docs/deepseek_integration.md`
- `docs/smoke_test_plan.md`
