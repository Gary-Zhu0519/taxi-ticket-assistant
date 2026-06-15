DROP VIEW IF EXISTS v_customer_service_ticket;
CREATE VIEW v_customer_service_ticket AS
SELECT
    t.ticket_id,
    c.complaint_id,
    ro.order_id,
    p.passenger_name,
    ct.type_name,
    c.complaint_status,
    t.ticket_status,
    t.priority_level,
    d.department_name,
    e.employee_name AS current_owner_name,
    t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN ride_order ro ON c.order_id = ro.order_id
JOIN passenger p ON c.passenger_id = p.passenger_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
LEFT JOIN employee e ON t.current_owner_id = e.employee_id
WHERE d.department_name = '客服部';

DROP VIEW IF EXISTS v_finance_complaint_ticket;
CREATE VIEW v_finance_complaint_ticket AS
SELECT
    t.ticket_id,
    ro.order_id,
    ct.type_name,
    t.priority_level,
    t.ticket_status,
    d.department_name,
    e.employee_name AS current_owner_name,
    t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN ride_order ro ON c.order_id = ro.order_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
LEFT JOIN employee e ON t.current_owner_id = e.employee_id
WHERE ct.type_name = '费用争议' OR d.department_name = '财务售后部';

DROP VIEW IF EXISTS v_safety_ticket;
CREATE VIEW v_safety_ticket AS
SELECT
    t.ticket_id,
    ro.order_id,
    ct.type_name,
    t.priority_level,
    t.ticket_status,
    d.department_name,
    e.employee_name AS current_owner_name,
    t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN ride_order ro ON c.order_id = ro.order_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
LEFT JOIN employee e ON t.current_owner_id = e.employee_id
WHERE ct.type_name = '安全事件' OR t.priority_level = 'P1' OR d.department_name = '安全部';

DROP VIEW IF EXISTS v_operation_ticket;
CREATE VIEW v_operation_ticket AS
SELECT
    t.ticket_id,
    ro.order_id,
    ct.type_name,
    t.priority_level,
    t.ticket_status,
    d.department_name,
    e.employee_name AS current_owner_name,
    t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN ride_order ro ON c.order_id = ro.order_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
LEFT JOIN employee e ON t.current_owner_id = e.employee_id
WHERE ct.type_name IN ('司机服务', '取消争议') OR d.department_name = '运营部';

DROP VIEW IF EXISTS v_manager_ticket_summary;
CREATE VIEW v_manager_ticket_summary AS
SELECT
    d.department_name,
    COUNT(t.ticket_id) AS total_tickets,
    SUM(CASE WHEN t.ticket_status IN ('处理中', '已升级', '待反馈', '已重开') THEN 1 ELSE 0 END) AS processing_tickets,
    SUM(CASE WHEN t.ticket_status = '已关闭' THEN 1 ELSE 0 END) AS closed_tickets,
    SUM(CASE WHEN t.ticket_status <> '已关闭' AND t.sla_deadline < datetime('now', 'localtime') THEN 1 ELSE 0 END) AS overdue_tickets
FROM department d
LEFT JOIN ticket t ON d.department_id = t.department_id
GROUP BY d.department_id, d.department_name
ORDER BY d.department_name;

DROP VIEW IF EXISTS v_employee_pending_ticket;
CREATE VIEW v_employee_pending_ticket AS
SELECT
    t.ticket_id,
    t.current_owner_id,
    e.employee_name AS current_owner_name,
    d.department_name,
    ct.type_name,
    t.priority_level,
    t.ticket_status,
    t.sla_deadline
FROM ticket t
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN department d ON t.department_id = d.department_id
JOIN employee e ON t.current_owner_id = e.employee_id
WHERE t.current_owner_id IS NOT NULL AND t.ticket_status <> '已关闭';

DROP VIEW IF EXISTS v_feedback_result;
CREATE VIEW v_feedback_result AS
SELECT
    f.feedback_id,
    f.ticket_id,
    ro.order_id,
    p.passenger_name,
    p.phone AS passenger_phone,
    ct.type_name,
    f.satisfaction_score,
    f.feedback_content,
    f.feedback_time
FROM feedback f
JOIN ticket t ON f.ticket_id = t.ticket_id
JOIN complaint c ON t.complaint_id = c.complaint_id
JOIN complaint_type ct ON c.complaint_type_id = ct.complaint_type_id
JOIN ride_order ro ON c.order_id = ro.order_id
JOIN passenger p ON f.passenger_id = p.passenger_id;
