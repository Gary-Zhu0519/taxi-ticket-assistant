# ER 图文字说明

本系统围绕“订单投诉 -> 后台工单 -> 历史过程记录 -> 反馈闭环”设计 ER 关系，核心关系如下：

1. `Passenger` 1 对多 `Ride_Order`
   一个乘客可以产生多笔订单，每笔订单只属于一个乘客。

2. `Driver` 1 对多 `Ride_Order`
   一个司机可以服务多笔订单，每笔订单只关联一个司机。

3. `Ride_Order` 1 对多 `Complaint`
   订单作为投诉背景，一笔订单可对应多次投诉。

4. `Complaint_Type` 1 对多 `Complaint`
   一个投诉类型可被多个投诉引用，承担分类字典和默认规则配置作用。

5. `Complaint` 1 对 1 `Ticket`
   每条投诉创建后都会自动生成一条工单，形成后台处理任务。

6. `Department` 1 对多 `Employee`
   一个部门可以拥有多名员工，员工只属于一个部门。

7. `Department` 1 对多 `Ticket`
   工单在任一时刻只对应一个当前责任部门，部门可负责多张工单。

8. `Employee` 1 对多 `Assignment_Record`
   员工作为分派人或接收人，会参与多条工单分派记录。

9. `Employee` 1 对多 `Action_Log`
   员工可对多张工单写入多条处理日志。

10. `Ticket` 1 对多 `Assignment_Record`
    一张工单在生命周期中可以多次分派，因此会有多条分派记录。

11. `Ticket` 1 对多 `Escalation_Record`
    高优先级、复杂或超时工单可以多次升级，形成升级历史。

12. `Ticket` 1 对多 `Action_Log`
    一张工单可以包含多条处理日志，用于记录完整处理过程。

13. `Ticket` 1 对 0 或 1 `Feedback`
    工单处理完成后可录入一条反馈记录，也可能尚未收到反馈。

补充说明：

- `Complaint_Type` 通过 `default_department_id` 关联 `Department`，用于确定默认责任部门。
- `Department` 通过 `manager_id` 关联 `Employee`，用于标记该部门主管。
- `Ticket.current_owner_id` 关联 `Employee`，表示当前负责人。
- `Feedback.passenger_id` 回指 `Passenger`，表示评价来源乘客。
