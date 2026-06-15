# Smoke Test Plan

## 1. 测试目标

验证课程项目在本地演示环境中能够完成：

- 应用启动
- 数据库初始化
- 页面渲染
- 权限控制
- 闭环业务流
- AI 助手只读查询

## 2. 测试范围

测试文件：

- `tests/test_smoke_app.py`
- `tests/test_smoke_database.py`
- `tests/test_smoke_views.py`
- `tests/test_smoke_permissions.py`
- `tests/test_smoke_workflow.py`
- `tests/test_smoke_ai_assistant.py`

## 3. 数据库表测试

验证：

1. 数据库文件可创建
2. 12 张核心表存在
3. 每张表可 count
4. 关键测试数据数量正确
5. 核心外键关系可访问

## 4. SQL 视图测试

验证以下视图可执行 `SELECT * LIMIT 5`：

- `v_customer_service_ticket`
- `v_finance_complaint_ticket`
- `v_safety_ticket`
- `v_operation_ticket`
- `v_manager_ticket_summary`
- `v_employee_pending_ticket`
- `v_feedback_result`

## 5. 页面路由测试

验证以下页面可打开并包含关键文本：

- `/login`
- `/`
- `/orders`
- `/orders/<order_id>`
- `/complaints`
- `/complaints/new`
- `/tickets`
- `/tickets/<ticket_id>`
- `/tickets/<ticket_id>/assign`
- `/tickets/<ticket_id>/escalate`
- `/tickets/<ticket_id>/log`
- `/tickets/<ticket_id>/feedback`
- `/dashboard`
- `/schema`
- `/assistant`

## 6. 权限测试

验证：

- 未登录访问受保护页面会跳转登录
- `admin / manager` 可访问 dashboard
- `finance` 不能看到非财务范围敏感工单
- `safety` 可看到 P1 或安全事件工单
- `employee` 只能看到自己的未关闭待办

## 7. 完整业务流测试

验证：

1. 创建投诉
2. 自动生成工单
3. 分派工单
4. 新增处理日志
5. 升级工单
6. 设为待反馈
7. 高分反馈自动关闭
8. 低分反馈自动重开

## 8. AI 助手测试

验证：

1. `/assistant` 页面可打开
2. 占位符 Key 时接口优雅返回，不报 500
3. 写操作请求被拒绝
4. employee 查询所有员工工单被拒绝
5. mock 模式下能返回结构化 JSON

## 9. Mock 测试与真实 API 测试区别

默认 smoke tests：

- 使用 monkeypatch mock DeepSeek
- 不消耗真实配额
- 不依赖外部网络

真实 API 测试：

- 仅在 `RUN_LIVE_AI_TESTS=true` 时开启
- 需要本地把 `DEEPSEEK_API_KEY` 替换为真实 Key
- 适合演示前最终自检
