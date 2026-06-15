-- 1. 查询某订单的所有投诉
SELECT c.complaint_id, ct.type_name, c.complaint_time, c.urgency_level, c.complaint_status
FROM complaint c
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
WHERE c.order_id = :order_id
ORDER BY c.complaint_time DESC;

-- 2. 查询某部门正在处理的工单
SELECT t.ticket_id, d.department_name, ct.type_name, t.priority_level, t.ticket_status
FROM ticket t
JOIN department d ON t.department_id = d.department_id
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
WHERE d.department_name = :department_name
  AND t.ticket_status IN ('处理中', '已升级', '待反馈', '已重开');

-- 3. 查询某员工当前待处理工单
SELECT ticket_id, type_name, priority_level, ticket_status, sla_deadline
FROM v_employee_pending_ticket
WHERE current_owner_id = :employee_id
ORDER BY sla_deadline;

-- 4. 查询所有超时未关闭工单
SELECT t.ticket_id, d.department_name, t.priority_level, t.ticket_status, t.sla_deadline
FROM ticket t
JOIN department d ON t.department_id = d.department_id
WHERE t.ticket_status <> '已关闭'
  AND t.sla_deadline < datetime('now', 'localtime')
ORDER BY t.sla_deadline;

-- 5. 按投诉类型统计投诉数量
SELECT ct.type_name, COUNT(c.complaint_id) AS complaint_count
FROM complaint_type ct
LEFT JOIN complaint c ON ct.complaint_type_id = c.complaint_type_id
GROUP BY ct.complaint_type_id, ct.type_name
ORDER BY complaint_count DESC;

-- 6. 按部门统计工单处理情况
SELECT * FROM v_manager_ticket_summary;

-- 7. 查询 P1 高优先级工单
SELECT t.ticket_id, ct.type_name, d.department_name, t.ticket_status, t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
WHERE t.priority_level = 'P1'
ORDER BY t.sla_deadline;

-- 8. 查询某工单的完整处理日志
SELECT l.log_id, l.action_type, l.action_content, l.action_time, e.employee_name
FROM action_log l
JOIN employee e ON l.employee_id = e.employee_id
WHERE l.ticket_id = :ticket_id
ORDER BY l.action_time;

-- 9. 查询满意度低于 3 的反馈记录
SELECT *
FROM v_feedback_result
WHERE satisfaction_score < 3
ORDER BY feedback_time DESC;

-- 10. 查询某乘客历史投诉记录
SELECT c.complaint_id, c.order_id, ct.type_name, c.complaint_time, c.complaint_status
FROM complaint c
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
WHERE c.passenger_id = :passenger_id
ORDER BY c.complaint_time DESC;

-- 11. 查询每个部门平均处理时长
SELECT
    d.department_name,
    ROUND(AVG((julianday(t.close_time) - julianday(t.create_time)) * 24), 2) AS avg_hours
FROM ticket t
JOIN department d ON t.department_id = d.department_id
WHERE t.close_time IS NOT NULL
GROUP BY d.department_id, d.department_name
ORDER BY avg_hours;

-- 12. 查询已升级工单及升级原因
SELECT t.ticket_id, ct.type_name, e.from_level, e.to_level, e.escalation_reason, e.escalation_time
FROM escalation_record e
JOIN ticket t ON e.ticket_id = t.ticket_id
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
ORDER BY e.escalation_time DESC;

-- 13. 查询费用争议类工单
SELECT * FROM v_finance_complaint_ticket;

-- 14. 查询安全事件工单
SELECT * FROM v_safety_ticket;

-- 15. 查询已关闭但满意度较低的工单
SELECT
    t.ticket_id,
    f.satisfaction_score,
    f.feedback_content,
    t.close_time
FROM ticket t
JOIN feedback f ON t.ticket_id = f.ticket_id
WHERE t.ticket_status = '已关闭'
  AND f.satisfaction_score < 3;
