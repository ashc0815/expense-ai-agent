"""7 个测试场景的报销单工厂函数。

Case 1: 正常报销（全A通过）
Case 2: 重复发票（Skill-01拦截）
Case 3: 超标拒绝（C级）
Case 4: 警告通过（B级）
Case 5: ConcurShield — 城市+描述模糊（核心showcase）
Case 6: ConcurShield — 模式异常
Case 7: 员工等级差异（配置驱动验证）
"""

from datetime import date, datetime

from models.enums import EmployeeLevel, InvoiceType, ReportStatus
from models.expense import Employee, ExpenseReport, Invoice, LineItem


# ------------------------------------------------------------------
# 通用工厂
# ------------------------------------------------------------------

def make_employee(
    level: EmployeeLevel = EmployeeLevel.L1,
    name: str = "张三",
    emp_id: str = "EMP-001",
) -> Employee:
    return Employee(
        name=name,
        id=emp_id,
        department="销售部",
        city="上海",
        hire_date=date(2022, 3, 15),
        bank_account="6222000000012345678",
        level=level,
    )


def _make_invoice(
    code: str, number: str, amount: float, city: str,
    inv_date: date = date(2025, 6, 3),
    inv_type: InvoiceType = InvoiceType.NORMAL,
    tax: float = 0.0,
) -> Invoice:
    return Invoice(
        invoice_code=code,
        invoice_number=number,
        invoice_type=inv_type,
        amount=amount,
        tax_amount=tax,
        date=inv_date,
        vendor="测试商户",
        city=city,
        items=["费用"],
        buyer_name="示例科技有限公司",
    )


# ------------------------------------------------------------------
# Case 1: 正常报销
# ------------------------------------------------------------------

def case1_normal_report() -> ExpenseReport:
    """L1员工上海出差，住宿480/晚+餐费80/人。
    L1上海住宿限额500，餐费限额100——全部在限额内。
    预期：全A级，COMPLETED。
    """
    emp = make_employee()
    inv_hotel = _make_invoice(
        "031001900301", "10000001", 480.0, "上海",
        inv_type=InvoiceType.SPECIAL, tax=28.8,
    )
    inv_meal = _make_invoice("031001900302", "10000002", 80.0, "上海")

    items = [
        LineItem(
            expense_type="accommodation", amount=480.0, currency="CNY",
            city="上海", date=date(2025, 6, 3), invoice=inv_hotel,
            description="上海客户拜访出差住宿一晚标准间",
        ),
        LineItem(
            expense_type="meals", amount=80.0, currency="CNY",
            city="上海", date=date(2025, 6, 3), invoice=inv_meal,
            description="上海客户拜访期间工作午餐一人份",
        ),
    ]
    return ExpenseReport(
        report_id="RPT-CASE1", employee=emp, line_items=items,
        total_amount=560.0, submit_date=datetime(2025, 6, 4, 9, 0),
    )


# ------------------------------------------------------------------
# Case 2: 重复发票
# ------------------------------------------------------------------

def case2_duplicate_invoice() -> ExpenseReport:
    """同一张发票提交两次（同code+number）。
    预期：Skill-01发票验证阶段拦截，REJECTED。
    """
    emp = make_employee()
    inv = _make_invoice("031001900311", "20000001", 200.0, "上海")

    items = [
        LineItem(
            expense_type="meals", amount=200.0, currency="CNY",
            city="上海", date=date(2025, 6, 3), invoice=inv,
            description="上海项目组团建工作午餐第一笔",
        ),
        LineItem(
            expense_type="meals", amount=200.0, currency="CNY",
            city="上海", date=date(2025, 6, 3), invoice=inv,  # 同一张发票！
            description="上海项目组团建工作午餐第二笔重复",
        ),
    ]
    return ExpenseReport(
        report_id="RPT-CASE2", employee=emp, line_items=items,
        total_amount=400.0, submit_date=datetime(2025, 6, 4, 9, 0),
    )


# ------------------------------------------------------------------
# Case 3: 超标拒绝（C级）
# ------------------------------------------------------------------

def case3_over_limit_reject() -> ExpenseReport:
    """L1员工二线城市(成都)住宿420/晚。
    限额350，超标70 > tolerance 50 → C级拒绝。
    预期：合规检查阶段REJECTED。
    """
    emp = make_employee()
    inv = _make_invoice("031001900321", "30000001", 420.0, "成都")

    items = [
        LineItem(
            expense_type="accommodation", amount=420.0, currency="CNY",
            city="成都", date=date(2025, 6, 3), invoice=inv,
            description="成都分公司会议期间住宿一晚标准间",
        ),
    ]
    return ExpenseReport(
        report_id="RPT-CASE3", employee=emp, line_items=items,
        total_amount=420.0, submit_date=datetime(2025, 6, 4, 9, 0),
    )


# ------------------------------------------------------------------
# Case 4: 警告通过（B级）
# ------------------------------------------------------------------

def case4_warning_pass() -> ExpenseReport:
    """L1员工二线城市(成都)住宿380/晚。
    限额350，超标30 ≤ tolerance 50 → B级警告通过。
    预期：合规B级但流程继续，COMPLETED。
    """
    emp = make_employee()
    inv = _make_invoice("031001900331", "40000001", 380.0, "成都")

    items = [
        LineItem(
            expense_type="accommodation", amount=380.0, currency="CNY",
            city="成都", date=date(2025, 6, 3), invoice=inv,
            description="成都分公司会议期间住宿一晚略超标准",
        ),
    ]
    return ExpenseReport(
        report_id="RPT-CASE4", employee=emp, line_items=items,
        total_amount=380.0, submit_date=datetime(2025, 6, 4, 9, 0),
    )


# ------------------------------------------------------------------
# Case 5: ConcurShield — 城市+描述模糊（核心showcase）
# ------------------------------------------------------------------

def case5_shield_ambiguity() -> ExpenseReport:
    """核心showcase：多个模糊因素叠加触发Shield。

    - 城市填 "Shanghai"（非标准中文，CityNormalizer → "上海" tier_1）
    - 费用类型 client_meal（entertainment，requires_attendee_list）
    - 描述 "商务活动费用"（含泛化词"费用"，<10字 → vague 100分）
    - 金额 90（L1 tier_1 meals_per_person 限额100，90%在边界 → boundary 100分）
    - 发生在周六（time_anomaly → 100分）
    - 没有附参会人名单（extra_checks 失败）

    模糊评分 ≈ 0.25*100 + 0.20*100 + 0.25*0 + 0.15*100 + 0.15*50
             = 25 + 20 + 0 + 15 + 7.5 = 67.5 → human_review
    合规A级（90<100）但 shield_triggered → PENDING_REVIEW。
    """
    emp = make_employee()
    inv = _make_invoice(
        "031001900341", "50000001", 90.0, "Shanghai",
        inv_date=date(2025, 6, 7),  # Saturday
    )

    items = [
        LineItem(
            expense_type="client_meal", amount=90.0, currency="CNY",
            city="Shanghai", date=date(2025, 6, 7),  # 周六
            invoice=inv,
            description="商务活动费用",  # 含"费用"泛化词 + <10字
            attendees=[],  # 招待费缺少参会人名单
        ),
    ]
    return ExpenseReport(
        report_id="RPT-CASE5", employee=emp, line_items=items,
        total_amount=90.0, submit_date=datetime(2025, 6, 8, 9, 0),
    )


def case5_with_history() -> list[LineItem]:
    """Case 5 附加历史数据——注入后可触发模式异常，score > 70。"""
    base = date(2025, 6, 7)
    return [
        LineItem(expense_type="client_meal", amount=88.0, currency="CNY",
                 city="Shanghai", date=date(2025, 6, 4), invoice=None,
                 description="客户会面工作午餐标准餐费"),
        LineItem(expense_type="client_meal", amount=92.0, currency="CNY",
                 city="Shanghai", date=date(2025, 6, 5), invoice=None,
                 description="客户会面工作午餐标准餐费"),
        LineItem(expense_type="client_meal", amount=85.0, currency="CNY",
                 city="Shanghai", date=date(2025, 6, 6), invoice=None,
                 description="客户会面工作午餐标准餐费"),
    ]


# ------------------------------------------------------------------
# Case 6: ConcurShield — 模式异常
# ------------------------------------------------------------------

def case6_pattern_anomaly() -> tuple[ExpenseReport, list[LineItem]]:
    """5天内3笔相似金额(88/92/85)餐费。
    每笔在限额内(L1上海100)，规则引擎全A。
    但 AmbiguityDetector 检测到模式异常 → human_review。

    Returns:
        (当前报销单, 历史行项目列表)
    """
    emp = make_employee()
    inv = _make_invoice("031001900351", "60000001", 88.0, "上海")

    items = [
        LineItem(
            expense_type="meals", amount=88.0, currency="CNY",
            city="上海", date=date(2025, 6, 5), invoice=inv,
            description="上海办公室日常工作午餐一人份",
        ),
    ]
    report = ExpenseReport(
        report_id="RPT-CASE6", employee=emp, line_items=items,
        total_amount=88.0, submit_date=datetime(2025, 6, 6, 9, 0),
    )

    history = [
        LineItem(expense_type="meals", amount=92.0, currency="CNY",
                 city="上海", date=date(2025, 6, 2), invoice=None,
                 description="上海办公室日常工作午餐一人份"),
        LineItem(expense_type="meals", amount=85.0, currency="CNY",
                 city="上海", date=date(2025, 6, 3), invoice=None,
                 description="上海办公室日常工作午餐一人份"),
        LineItem(expense_type="meals", amount=90.0, currency="CNY",
                 city="上海", date=date(2025, 6, 4), invoice=None,
                 description="上海办公室日常工作午餐一人份"),
    ]
    return report, history


# ------------------------------------------------------------------
# Case 7: 员工等级差异
# ------------------------------------------------------------------

def case7_level_comparison() -> tuple[ExpenseReport, ExpenseReport]:
    """同一笔住宿500/晚在二线城市(成都)。
    L1限额350 → 超标150 → C级拒绝。
    L2限额500 → 刚好等于限额 → A级通过。

    Returns:
        (L1报销单, L2报销单)
    """
    def _make(level: EmployeeLevel, report_id: str) -> ExpenseReport:
        emp = make_employee(level=level, name=f"测试{level.value}",
                            emp_id=f"EMP-{level.value}")
        inv = _make_invoice(
            "031001900361" if level == EmployeeLevel.L1 else "031001900362",
            "70000001" if level == EmployeeLevel.L1 else "70000002",
            500.0, "成都",
        )
        items = [
            LineItem(
                expense_type="accommodation", amount=500.0, currency="CNY",
                city="成都", date=date(2025, 6, 3), invoice=inv,
                description="成都分公司季度会议期间住宿一晚",
            ),
        ]
        return ExpenseReport(
            report_id=report_id, employee=emp, line_items=items,
            total_amount=500.0, submit_date=datetime(2025, 6, 4, 9, 0),
        )

    return _make(EmployeeLevel.L1, "RPT-CASE7-L1"), _make(EmployeeLevel.L2, "RPT-CASE7-L2")
