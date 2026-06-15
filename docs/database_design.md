# 数据库设计说明

## 1. 系统定位

本项目名称为“第三方打车平台后台订单投诉与工单闭环管理系统”。

系统不是完整打车平台，不实现实时派单、路线规划、动态计价、在线支付、司机接单、车辆管理等复杂前台业务。订单表 `Ride_Order` 仅作为投诉背景。系统重点是：

- 乘客基于订单发起投诉
- 投诉自动生成后台工单
- 工单根据投诉类型默认规则进入对应部门
- 员工记录处理过程、分派、升级和反馈
- 最终形成后台投诉工单闭环

## 2. Workflow

完整业务主线如下：

1. 订单作为投诉来源。
2. 后台人员在订单详情页创建投诉。
3. 系统根据 `Complaint_Type` 自动生成 `Ticket`。
4. 工单进入“待分派”状态。
5. 管理员、主管或客服分派给具体员工。
6. 处理员工新增 `Action_Log`，记录沟通、核查、退款、处罚等动作。
7. 高优先级、超时或责任复杂工单可新增 `Escalation_Record`。
8. 处理完成后将工单改为“待反馈”。
9. 后台模拟录入乘客 `Feedback`。
10. 满意度 >= 3 时工单关闭，满意度 < 3 时工单重开。

## 3. 12 张表结构

### 3.1 Passenger

- 主键：`passenger_id`
- 关键字段：`passenger_name`, `phone`, `account_status`, `created_at`
- 用途：保存投诉来源乘客信息。

### 3.2 Driver

- 主键：`driver_id`
- 关键字段：`driver_name`, `phone`, `driver_score`, `driver_status`, `created_at`
- 用途：保存订单关联司机信息。

### 3.3 Ride_Order

- 主键：`order_id`
- 外键：`passenger_id -> Passenger`, `driver_id -> Driver`
- 关键字段：`start_location`, `end_location`, `order_time`, `finish_time`, `order_amount`, `order_status`
- 用途：保存订单背景，不实现完整打车交易流程。

### 3.4 Complaint_Type

- 主键：`complaint_type_id`
- 外键：`default_department_id -> Department`
- 关键字段：`type_name`, `type_description`, `default_priority_level`, `default_sla_hours`
- 用途：分类字典和默认规则表，用于自动确定默认责任部门、优先级和 SLA。

### 3.5 Complaint

- 主键：`complaint_id`
- 外键：`order_id -> Ride_Order`, `passenger_id -> Passenger`, `complaint_type_id -> Complaint_Type`
- 关键字段：`complaint_content`, `complaint_time`, `urgency_level`, `complaint_status`
- 用途：记录乘客提出的原始问题。

### 3.6 Department

- 主键：`department_id`
- 外键：`manager_id -> Employee`
- 关键字段：`department_name`, `department_type`
- 用途：定义投诉工单所属责任部门。

### 3.7 Employee

- 主键：`employee_id`
- 外键：`department_id -> Department`
- 关键字段：`employee_name`, `role`, `username`, `password`, `phone`, `employee_status`
- 用途：定义系统用户、角色与登录信息。

### 3.8 Ticket

- 主键：`ticket_id`
- 外键：`complaint_id -> Complaint`, `department_id -> Department`, `current_owner_id -> Employee`
- 关键字段：`priority_level`, `ticket_status`, `create_time`, `sla_deadline`, `close_time`
- 用途：记录后台对投诉的处理任务，是系统闭环主表。

### 3.9 Assignment_Record

- 主键：`assignment_id`
- 外键：`ticket_id -> Ticket`, `assigner_id -> Employee`, `receiver_id -> Employee`, `department_id -> Department`
- 关键字段：`assign_time`, `assignment_note`
- 用途：保存工单分派历史。

### 3.10 Escalation_Record

- 主键：`escalation_id`
- 外键：`ticket_id -> Ticket`, `escalated_by -> Employee`
- 关键字段：`from_level`, `to_level`, `escalation_reason`, `escalation_time`
- 用途：保存升级历史。

### 3.11 Action_Log

- 主键：`log_id`
- 外键：`ticket_id -> Ticket`, `employee_id -> Employee`
- 关键字段：`action_type`, `action_content`, `action_time`
- 用途：保存工单处理动作轨迹。

### 3.12 Feedback

- 主键：`feedback_id`
- 外键：`ticket_id -> Ticket`, `passenger_id -> Passenger`
- 关键字段：`satisfaction_score`, `feedback_content`, `feedback_time`
- 用途：记录乘客对处理结果的满意度评价。

## 4. 主键和外键

- 所有主键均使用字符串型主键，便于课程展示时阅读和区分业务实体。
- `Ride_Order` 强制关联有效乘客和司机。
- `Complaint` 强制关联有效订单、乘客和投诉类型。
- `Ticket` 强制关联有效投诉，并与投诉形成 1 对 1 关系。
- `Assignment_Record`、`Escalation_Record`、`Action_Log`、`Feedback` 都通过外键回指 `Ticket`。

## 5. 完整性约束

模型层和数据库层实现了以下关键约束：

- `Passenger.passenger_id`、`Driver.driver_id` 非空唯一。
- `Feedback.satisfaction_score` 通过检查约束限制在 1 到 5 之间。
- `Ticket.priority_level` 仅允许 `P1`、`P2`、`P3`、`P4`。
- `Ticket.ticket_status` 仅允许 `待分派`、`处理中`、`已升级`、`待反馈`、`已关闭`、`已重开`。
- `Complaint.urgency_level` 仅允许 `U1`、`U2`、`U3`、`U4`。
- 每个工单最多一条反馈记录，`Feedback.ticket_id` 设置唯一约束。
- 工单关闭前，服务层强制要求至少存在一条 `Action_Log`。
- 已关闭工单不能新增处理日志，必须先重开。

## 6. 视图设计

系统初始化时创建以下视图：

- `v_customer_service_ticket`
  客服部工单视图，汇总投诉、订单、乘客、投诉类型、工单状态、优先级、负责人和 SLA。

- `v_finance_complaint_ticket`
  财务售后视图，聚焦费用争议及财务售后相关工单。

- `v_safety_ticket`
  安全部门视图，展示安全事件和 P1 工单。

- `v_operation_ticket`
  运营部门视图，展示司机服务与取消争议等运营相关工单。

- `v_manager_ticket_summary`
  主管统计视图，按部门汇总工单总数、处理中数量、已关闭数量、超时数量。

- `v_employee_pending_ticket`
  员工个人待办视图，展示未关闭且存在当前负责人的工单。

- `v_feedback_result`
  反馈结果视图，汇总满意度、投诉类型、订单编号和乘客信息。

这些视图已在首页和统计看板页面直接使用，用于体现不同角色的查询范围。

## 7. 索引设计

按照课程要求创建以下索引：

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

这些索引主要优化了按订单、投诉类型、工单状态、负责人、部门以及工单历史记录的查询。

## 8. 安全性设计

系统采用轻量但适合课程展示的安全设计：

- 使用 Flask Session 完成登录状态维护。
- 密码使用 Werkzeug 哈希保存。
- 通过角色控制页面入口和查询范围。
- 页面层与查询层同时限制数据访问。
- 普通员工只能查看本人负责的未关闭工单。
- 主管和管理员才可访问统计看板。

## 9. 典型 SQL 查询说明

项目 `docs/sql_examples.sql` 已给出 15 条典型查询，包括：

- 查询某订单的所有投诉
- 查询某部门正在处理的工单
- 查询某员工待处理工单
- 查询超时未关闭工单
- 按投诉类型和部门统计
- 查询 P1 工单
- 查询完整处理日志
- 查询低满意度反馈
- 查询乘客历史投诉
- 查询平均处理时长
- 查询升级工单
- 查询费用争议、安全事件和低满意度相关结果

这些 SQL 既可用于课程答辩展示，也可以直接在 SQLite 客户端中执行。
