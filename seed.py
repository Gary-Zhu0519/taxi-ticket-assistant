from __future__ import annotations

from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from database import db, describe_current_database, drop_supporting_objects, ensure_supporting_objects
from models import ComplaintType, Department, Driver, Employee, Passenger, RideOrder, Ticket
from services import (
    add_action_log,
    assign_ticket,
    create_complaint_and_ticket,
    escalate_ticket,
    reopen_ticket,
    set_ticket_pending_feedback,
    submit_feedback,
)


def seed_departments():
    departments = [
        Department(department_id="DEP001", department_name="客服部", department_type="业务支持"),
        Department(department_id="DEP002", department_name="财务售后部", department_type="财务售后"),
        Department(department_id="DEP003", department_name="安全部", department_type="安全风控"),
        Department(department_id="DEP004", department_name="运营部", department_type="平台运营"),
        Department(department_id="DEP005", department_name="技术支持部", department_type="技术保障"),
        Department(department_id="DEP006", department_name="系统管理部", department_type="系统管理"),
    ]
    db.session.add_all(departments)
    db.session.flush()
    return {department.department_name: department for department in departments}


def seed_complaint_types(departments):
    complaint_types = [
        ComplaintType(
            complaint_type_id="CT001",
            type_name="费用争议",
            type_description="绕路、多收费、价格异常。",
            default_department_id=departments["财务售后部"].department_id,
            default_priority_level="P2",
            default_sla_hours=24,
        ),
        ComplaintType(
            complaint_type_id="CT002",
            type_name="司机服务",
            type_description="态度差、辱骂、拒载。",
            default_department_id=departments["运营部"].department_id,
            default_priority_level="P2",
            default_sla_hours=24,
        ),
        ComplaintType(
            complaint_type_id="CT003",
            type_name="安全事件",
            type_description="危险驾驶、骚扰、冲突。",
            default_department_id=departments["安全部"].department_id,
            default_priority_level="P1",
            default_sla_hours=2,
        ),
        ComplaintType(
            complaint_type_id="CT004",
            type_name="取消争议",
            type_description="司机或乘客取消责任争议。",
            default_department_id=departments["客服部"].department_id,
            default_priority_level="P3",
            default_sla_hours=48,
        ),
        ComplaintType(
            complaint_type_id="CT005",
            type_name="物品遗失",
            type_description="乘客遗失物品寻找。",
            default_department_id=departments["客服部"].department_id,
            default_priority_level="P3",
            default_sla_hours=48,
        ),
        ComplaintType(
            complaint_type_id="CT006",
            type_name="平台异常",
            type_description="派单错误、系统故障、定位异常。",
            default_department_id=departments["技术支持部"].department_id,
            default_priority_level="P2",
            default_sla_hours=24,
        ),
        ComplaintType(
            complaint_type_id="CT007",
            type_name="其他问题",
            type_description="无法归类的投诉。",
            default_department_id=departments["客服部"].department_id,
            default_priority_level="P4",
            default_sla_hours=72,
        ),
    ]
    db.session.add_all(complaint_types)
    db.session.flush()
    return {item.type_name: item for item in complaint_types}


def seed_employees(departments):
    employees = [
        Employee(
            employee_id="EMP001",
            employee_name="系统管理员",
            department_id=departments["系统管理部"].department_id,
            role="admin",
            username="admin",
            password=generate_password_hash("admin123"),
            phone="13800010001",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP002",
            employee_name="总控主管",
            department_id=departments["系统管理部"].department_id,
            role="manager",
            username="manager",
            password=generate_password_hash("manager123"),
            phone="13800010002",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP003",
            employee_name="客服专员李晴",
            department_id=departments["客服部"].department_id,
            role="customer_service",
            username="service",
            password=generate_password_hash("service123"),
            phone="13800010003",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP004",
            employee_name="财务专员周宁",
            department_id=departments["财务售后部"].department_id,
            role="finance",
            username="finance",
            password=generate_password_hash("finance123"),
            phone="13800010004",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP005",
            employee_name="安全专员顾言",
            department_id=departments["安全部"].department_id,
            role="safety",
            username="safety",
            password=generate_password_hash("safety123"),
            phone="13800010005",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP006",
            employee_name="运营专员陈诺",
            department_id=departments["运营部"].department_id,
            role="operation",
            username="operation",
            password=generate_password_hash("operation123"),
            phone="13800010006",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP007",
            employee_name="客服员工赵川",
            department_id=departments["客服部"].department_id,
            role="employee",
            username="employee",
            password=generate_password_hash("employee123"),
            phone="13800010007",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP008",
            employee_name="技术支持林珩",
            department_id=departments["技术支持部"].department_id,
            role="employee",
            username="tech1",
            password=generate_password_hash("tech123"),
            phone="13800010008",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP009",
            employee_name="财务员工高原",
            department_id=departments["财务售后部"].department_id,
            role="employee",
            username="finance2",
            password=generate_password_hash("finance223"),
            phone="13800010009",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP010",
            employee_name="安全员工苏禾",
            department_id=departments["安全部"].department_id,
            role="employee",
            username="safety2",
            password=generate_password_hash("safety223"),
            phone="13800010010",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP011",
            employee_name="运营员工江越",
            department_id=departments["运营部"].department_id,
            role="employee",
            username="operation2",
            password=generate_password_hash("operation223"),
            phone="13800010011",
            employee_status="在职",
        ),
        Employee(
            employee_id="EMP012",
            employee_name="客服员工何沫",
            department_id=departments["客服部"].department_id,
            role="employee",
            username="service2",
            password=generate_password_hash("service223"),
            phone="13800010012",
            employee_status="在职",
        ),
    ]
    db.session.add_all(employees)
    db.session.flush()

    manager_id = "EMP002"
    for department in departments.values():
        department.manager_id = manager_id

    return {employee.employee_id: employee for employee in employees}


def seed_passengers():
    passengers = []
    for idx, name in enumerate(
        ["张敏", "李娟", "王凯", "陈晨", "赵玲", "周航", "吴悦", "郑浩", "孙宁", "何倩"],
        start=1,
    ):
        passengers.append(
            Passenger(
                passenger_id=f"PSG{idx:03d}",
                passenger_name=name,
                phone=f"13900020{idx:03d}",
                account_status="正常",
                created_at=datetime.now() - timedelta(days=120 - idx),
            )
        )
    db.session.add_all(passengers)
    db.session.flush()
    return {item.passenger_id: item for item in passengers}


def seed_drivers():
    drivers = []
    names = ["刘强", "马超", "沈飞", "彭浩", "易峰", "贺晨", "戴林", "罗成"]
    for idx, name in enumerate(names, start=1):
        drivers.append(
            Driver(
                driver_id=f"DRV{idx:03d}",
                driver_name=name,
                phone=f"13700030{idx:03d}",
                driver_score=4.1 + idx * 0.08,
                driver_status="在岗" if idx % 4 else "观察中",
                created_at=datetime.now() - timedelta(days=200 - idx * 3),
            )
        )
    db.session.add_all(drivers)
    db.session.flush()
    return {item.driver_id: item for item in drivers}


def seed_orders(passengers, drivers):
    start_points = [
        "福田高铁站",
        "南山科技园",
        "罗湖口岸",
        "宝安机场",
        "前海湾",
        "深圳北站",
        "车公庙",
        "大学城",
        "蛇口码头",
        "华强北",
    ]
    end_points = [
        "腾讯滨海大厦",
        "会展中心",
        "万象天地",
        "龙岗大运中心",
        "后海总部基地",
        "福田CBD",
        "坂田华为基地",
        "欢乐海岸",
        "人才公园",
        "南头古城",
    ]
    status_list = ["已完成", "已完成", "已完成", "已完成", "已取消", "异常", "进行中"]
    orders = []
    base_time = datetime.now() - timedelta(days=10)
    passenger_keys = list(passengers.keys())
    driver_keys = list(drivers.keys())

    for idx in range(1, 21):
        order_time = base_time + timedelta(hours=idx * 5)
        order_status = status_list[idx % len(status_list)]
        finish_time = order_time + timedelta(minutes=35 + idx) if order_status == "已完成" else None
        orders.append(
            RideOrder(
                order_id=f"ORD{idx:03d}",
                passenger_id=passenger_keys[(idx - 1) % len(passenger_keys)],
                driver_id=driver_keys[(idx - 1) % len(driver_keys)],
                start_location=start_points[(idx - 1) % len(start_points)],
                end_location=end_points[(idx - 1) % len(end_points)],
                order_time=order_time,
                finish_time=finish_time,
                order_amount=18.5 + idx * 3.25,
                order_status=order_status,
            )
        )
    db.session.add_all(orders)
    db.session.flush()
    return {item.order_id: item for item in orders}


def seed_complaints_and_tickets(orders, complaint_types, employees):
    scenarios = [
        ("ORD001", "费用争议", "U2", "乘客认为存在绕路，多收取 18 元。", "EMP004", "closed_high"),
        ("ORD002", "司机服务", "U2", "司机态度恶劣，途中与乘客争执。", "EMP011", "processing"),
        ("ORD003", "安全事件", "U1", "司机频繁急刹并在高架上接打电话。", "EMP010", "escalated"),
        ("ORD004", "取消争议", "U3", "司机到达后要求乘客主动取消。", "EMP007", "closed"),
        ("ORD005", "物品遗失", "U3", "乘客将电脑包遗落在后排座位。", "EMP012", "pending_feedback"),
        ("ORD006", "平台异常", "U2", "系统定位异常，订单起点与实际不符。", "EMP008", "processing_overdue"),
        ("ORD007", "其他问题", "U4", "客服回访中发现问题无法归类。", "EMP007", "reopened_feedback_low"),
        ("ORD008", "费用争议", "U2", "行程结束后价格异常上涨。", "EMP009", "processing"),
        ("ORD009", "司机服务", "U2", "司机拒绝按照导航行驶。", "EMP011", "closed"),
        ("ORD010", "安全事件", "U1", "乘客反映存在语言骚扰。", "EMP010", "closed_low"),
        ("ORD011", "取消争议", "U3", "乘客称未上车却被收取取消费。", "EMP003", "processing"),
        ("ORD012", "物品遗失", "U2", "遗失手机，需要紧急协助联系司机。", "EMP012", "escalated"),
        ("ORD013", "平台异常", "U2", "派单系统重复创建了两笔订单。", "EMP008", "closed"),
        ("ORD014", "其他问题", "U4", "希望补开发票但订单信息异常。", "EMP007", "pending_feedback"),
        ("ORD015", "费用争议", "U1", "司机私下要求补差价。", "EMP004", "escalated_overdue"),
        ("ORD016", "司机服务", "U2", "司机未按约定协助搬运行李。", "EMP006", "processing"),
        ("ORD017", "安全事件", "U1", "乘客与司机发生肢体冲突。", "EMP005", "closed_high"),
        ("ORD018", "取消争议", "U4", "乘客认为平台误判了取消责任。", "EMP003", "reopened_manual"),
        ("ORD019", "平台异常", "U2", "定位漂移导致司机无法准确接驾。", "EMP008", "processing_overdue"),
        ("ORD020", "其他问题", "U3", "乘客投诉客服回复不及时。", "EMP012", "closed"),
    ]

    created = {}
    base_time = datetime.now() - timedelta(days=5)

    for idx, (order_id, type_name, urgency, content, owner_id, scenario) in enumerate(scenarios, start=1):
        complaint_time = base_time + timedelta(hours=idx * 4)
        complaint, ticket = create_complaint_and_ticket(
            orders[order_id],
            complaint_types[type_name],
            content,
            urgency,
            complaint_time=complaint_time,
        )
        owner = employees[owner_id]
        assigner = employees["EMP003"] if type_name in {"取消争议", "物品遗失", "其他问题"} else employees["EMP002"]
        assign_ticket(ticket, assigner, owner, f"根据 {type_name} 流程分派。", assign_time=complaint_time + timedelta(minutes=15))
        add_action_log(
            ticket,
            owner,
            "核查订单",
            f"已核查订单 {order_id}，确认投诉类型为 {type_name}。",
            action_time=complaint_time + timedelta(hours=1),
        )

        if scenario in {"closed_high", "closed", "closed_low", "pending_feedback", "reopened_feedback_low", "reopened_manual"}:
            add_action_log(
                ticket,
                owner,
                "联系乘客",
                "已联系乘客说明处理方案，并记录诉求细节。",
                action_time=complaint_time + timedelta(hours=2),
            )
        if scenario in {"closed_high", "closed", "closed_low"}:
            set_ticket_pending_feedback(ticket, owner)

        if scenario == "closed_high":
            submit_feedback(
                ticket,
                5,
                "处理及时，沟通清晰，认可平台方案。",
                feedback_time=complaint_time + timedelta(hours=8),
            )
        elif scenario == "closed":
            submit_feedback(
                ticket,
                4,
                "问题已解决，整体满意。",
                feedback_time=complaint_time + timedelta(hours=10),
            )
        elif scenario == "closed_low":
            submit_feedback(
                ticket,
                2,
                "处理结果一般，希望继续跟进。",
                feedback_time=complaint_time + timedelta(hours=9),
            )
        elif scenario == "pending_feedback":
            set_ticket_pending_feedback(ticket, owner)
        elif scenario == "reopened_feedback_low":
            set_ticket_pending_feedback(ticket, owner)
            submit_feedback(
                ticket,
                1,
                "仍未收到有效解决方案，要求继续处理。",
                feedback_time=complaint_time + timedelta(hours=6),
            )
        elif scenario == "reopened_manual":
            set_ticket_pending_feedback(ticket, owner)
            reopen_ticket(ticket, employees["EMP002"])
        elif scenario in {"escalated", "escalated_overdue"}:
            add_action_log(
                ticket,
                owner,
                "联系司机",
                "已联系司机复盘事件经过，等待进一步佐证材料。",
                action_time=complaint_time + timedelta(hours=2),
            )
            escalate_ticket(
                ticket,
                employees["EMP002"],
                ticket.department.department_name,
                "主管复核",
                "涉及高优先级或责任不清，需要进一步升级处置。",
                escalation_time=complaint_time + timedelta(hours=3),
            )
        elif scenario in {"processing", "processing_overdue"}:
            add_action_log(
                ticket,
                owner,
                "联系司机",
                "正在与司机核对当时情况并等待补充材料。",
                action_time=complaint_time + timedelta(hours=3),
            )

        if scenario in {"processing_overdue", "escalated_overdue"}:
            ticket.sla_deadline = datetime.now() - timedelta(hours=4)
        if scenario == "processing":
            ticket.sla_deadline = datetime.now() + timedelta(hours=18)
        if scenario == "pending_feedback":
            ticket.sla_deadline = datetime.now() + timedelta(hours=6)
        if scenario == "reopened_feedback_low":
            ticket.sla_deadline = datetime.now() - timedelta(hours=2)
        if scenario == "reopened_manual":
            ticket.sla_deadline = datetime.now() + timedelta(hours=12)

        created[ticket.ticket_id] = ticket

    return created


def main():
    app = create_app()
    with app.app_context():
        drop_supporting_objects()
        db.drop_all()
        db.create_all()

        departments = seed_departments()
        complaint_types = seed_complaint_types(departments)
        employees = seed_employees(departments)
        passengers = seed_passengers()
        drivers = seed_drivers()
        orders = seed_orders(passengers, drivers)
        seed_complaints_and_tickets(orders, complaint_types, employees)

        db.session.commit()
        ensure_supporting_objects()

        print("Database seeded successfully.")
        print(f"Database target: {describe_current_database()}")
        print(f"Passengers: {Passenger.query.count()}")
        print(f"Drivers: {Driver.query.count()}")
        print(f"Orders: {RideOrder.query.count()}")
        print(f"Complaint Types: {ComplaintType.query.count()}")
        print(f"Departments: {Department.query.count()}")
        print(f"Employees: {Employee.query.count()}")
        print(f"Tickets: {Ticket.query.count()}")


if __name__ == "__main__":
    main()
