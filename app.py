"""Flask Web App — ConcurShield Agent 用户交互入口。

启动:
    python app.py
    # 然后浏览器打开 http://localhost:5000
"""

from __future__ import annotations

import sys
from dataclasses import is_dataclass, asdict
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request, send_from_directory

from config import ConfigLoader
from agent.controller import ExpenseController
from agent.ambiguity_detector import AmbiguityDetector
from mock_data.sample_reports import (
    case1_normal_report,
    case2_duplicate_invoice,
    case3_over_limit_reject,
    case4_warning_pass,
    case5_shield_ambiguity,
    case5_with_history,
    case6_pattern_anomaly,
    case7_level_comparison,
)
from models.enums import FinalStatus
from skills.skill_03_compliance import process as compliance_process
from skills.skill_04_voucher import reset_voucher_seq


app = Flask(__name__, static_folder=".", static_url_path="")


# ------------------------------------------------------------------
# 初始化
# ------------------------------------------------------------------

_loader: ConfigLoader = None  # type: ignore
_ctrl: ExpenseController = None  # type: ignore


def _init() -> None:
    global _loader, _ctrl
    if _loader is None:
        ConfigLoader.reset()
        _loader = ConfigLoader()
        _loader.load()
        _ctrl = ExpenseController(_loader)


# ------------------------------------------------------------------
# 预设场景
# ------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "case1": {
        "title": "Case 1: 正常报销",
        "subtitle": "L1 上海出差 480+80，全 A 级通过",
        "expected": "completed",
        "showcase": False,
        "factory": case1_normal_report,
    },
    "case2": {
        "title": "Case 2: 重复发票",
        "subtitle": "同一张发票提交两次，Skill-01 拦截",
        "expected": "rejected",
        "showcase": False,
        "factory": case2_duplicate_invoice,
    },
    "case3": {
        "title": "Case 3: 超标拒绝 (C级)",
        "subtitle": "L1 成都住宿 420，超限 70 > 50 → C 级",
        "expected": "rejected",
        "showcase": False,
        "factory": case3_over_limit_reject,
    },
    "case4": {
        "title": "Case 4: 警告通过 (B级)",
        "subtitle": "L1 成都住宿 380，超限 30 ≤ 50 → B 级",
        "expected": "completed",
        "showcase": False,
        "factory": case4_warning_pass,
    },
    "case5": {
        "title": "Case 5: Shield — 城市+描述模糊",
        "subtitle": "Shanghai + 商务活动费用 + 周六 + 无参会人",
        "expected": "pending_review",
        "showcase": True,
        "factory": case5_shield_ambiguity,
    },
    "case6": {
        "title": "Case 6: Shield — 模式异常",
        "subtitle": "5天内3笔相似餐费，规则 A 级但模式异常",
        "expected": "detected",
        "showcase": True,
        "factory": None,  # 特殊处理
    },
    "case7_l1": {
        "title": "Case 7a: L1 等级对比",
        "subtitle": "L1 成都住宿 500，限额 350 → 拒绝",
        "expected": "rejected",
        "showcase": True,
        "factory": lambda: case7_level_comparison()[0],
    },
    "case7_l2": {
        "title": "Case 7b: L2 等级对比",
        "subtitle": "L2 成都住宿 500，限额 500 → 通过",
        "expected": "completed",
        "showcase": True,
        "factory": lambda: case7_level_comparison()[1],
    },
}


# ------------------------------------------------------------------
# 序列化辅助
# ------------------------------------------------------------------

def _json_safe(obj):
    """递归转换 dataclass / enum / date / timedelta 为 JSON 可序列化对象。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds() * 1000  # ms
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if is_dataclass(obj):
        return _json_safe(asdict(obj))
    if hasattr(obj, "__dict__"):
        return _json_safe(vars(obj))
    return str(obj)


def _serialize_report(report) -> dict:
    return {
        "report_id": report.report_id,
        "employee": {
            "name": report.employee.name,
            "id": report.employee.id,
            "department": report.employee.department,
            "level": report.employee.level.value,
            "city": report.employee.city,
        },
        "total_amount": report.total_amount,
        "submit_date": report.submit_date.isoformat(),
        "line_items": [
            {
                "expense_type": item.expense_type,
                "amount": item.amount,
                "currency": item.currency,
                "city": item.city,
                "date": item.date.isoformat(),
                "description": item.description,
                "attendees": item.attendees,
                "has_invoice": item.invoice is not None,
                "invoice_code": item.invoice.invoice_code if item.invoice else None,
                "invoice_number": item.invoice.invoice_number if item.invoice else None,
                "invoice_type": item.invoice.invoice_type.value if item.invoice else None,
            }
            for item in report.line_items
        ],
    }


def _serialize_result(report, result) -> dict:
    return {
        "report": _serialize_report(report),
        "final_status": result.final_status.value,
        "total_ms": result.total_processing_time.total_seconds() * 1000,
        "timeline": [
            {
                "skill_name": s.skill_name,
                "display_name": s.display_name,
                "passed": s.passed,
                "skipped": s.skipped,
                "duration_ms": s.duration.total_seconds() * 1000,
                "message": s.message,
                "fail_action": s.fail_action,
                "detail": _extract_step_detail(s),
            }
            for s in result.timeline
        ],
        "shield_report": _json_safe(result.shield_report),
        "processing_log": report.processing_log,
    }


def _extract_step_detail(step) -> dict:
    """从 StepResult.detail 中提取关键信息（避免返回完整的嵌套 dataclass）。"""
    detail = step.detail or {}
    summary: dict = {"issues": detail.get("issues", [])}

    # compliance_result
    cmp = detail.get("compliance_result")
    if cmp:
        summary["compliance_level"] = cmp.overall_level.value
        summary["line_count"] = len(cmp.line_details)

    # approval_result
    app_r = detail.get("approval_result")
    if app_r:
        summary["approval_chain"] = [
            {"role": s.approver_role, "hours": s.actual_hours, "status": s.status}
            for s in app_r.approval_chain
        ]
        summary["escalation_events"] = app_r.escalation_events

    # voucher_result
    vr = detail.get("voucher_result")
    if vr:
        summary["voucher_number"] = vr.voucher_number
        summary["total_debit"] = vr.total_debit
        summary["balanced"] = vr.balanced
        summary["entries"] = [
            {"account": e.account, "direction": e.direction, "amount": e.amount}
            for e in vr.entries
        ]

    # payment_result
    pr = detail.get("payment_result")
    if pr:
        summary["payment_method"] = pr.payment_method
        summary["payment_ref"] = pr.payment_ref
        summary["payment_success"] = pr.success

    # receipt
    if step.skill_name == "receipt_validation":
        summary["receipt_results"] = [
            {
                "invoice_code": r.invoice.invoice_code,
                "normalized_city": r.normalized_city,
                "passed": r.passed,
            }
            for r in (detail.get("receipt_results") or [])
        ]

    return summary


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/dashboard.html")
def dashboard():
    return send_from_directory(".", "dashboard.html")


@app.route("/api/presets")
def presets():
    """返回所有预设场景列表。"""
    return jsonify([
        {
            "id": key,
            "title": v["title"],
            "subtitle": v["subtitle"],
            "expected": v["expected"],
            "showcase": v["showcase"],
        }
        for key, v in PRESETS.items()
    ])


@app.route("/api/config")
def config():
    """返回当前配置快照（审计 / 展示用）。"""
    _init()
    cfg = _loader.get_all()
    return jsonify({
        "policy": {
            "company": cfg["policy"].get("company_info", {}),
            "limits": cfg["policy"].get("limits", {}),
            "tolerance": cfg["policy"].get("tolerance", {}),
            "payment": cfg["policy"].get("payment", {}),
        },
        "workflow": cfg["workflow"],
        "city_mapping_count": len(cfg["city_mapping"].get("mappings", {})),
        "expense_types": list(cfg["expense_types"].get("expense_types", {}).keys()),
    })


@app.route("/api/process", methods=["POST"])
def process():
    """处理一个预设场景的报销单。"""
    _init()
    reset_voucher_seq()

    data = request.get_json() or {}
    preset_id = data.get("preset", "")

    if preset_id not in PRESETS:
        return jsonify({"error": f"未知预设: {preset_id}"}), 400

    preset = PRESETS[preset_id]

    # Case 6 特殊处理：直接调用 AmbiguityDetector，不走完整流程
    if preset_id == "case6":
        report, history = case6_pattern_anomaly()
        cmp_result = compliance_process(report)
        detector = AmbiguityDetector(_loader)
        amb = detector.evaluate(report.line_items[0], report.employee, [], history)
        return jsonify({
            "report": _serialize_report(report),
            "final_status": "detected",
            "is_case6": True,
            "compliance_level": cmp_result.overall_level.value,
            "ambiguity": {
                "score": amb.score,
                "recommendation": amb.recommendation,
                "triggered_factors": amb.triggered_factors,
                "explanation": amb.explanation,
                "llm_review": _json_safe(amb.llm_review) if amb.llm_review else None,
            },
            "history_count": len(history),
        })

    # 其他 case：走完整流程
    report = preset["factory"]()
    result = _ctrl.process_report(report)
    response = _serialize_result(report, result)

    # Case 5 附加：展示注入历史数据后 score > 70 的情况
    if preset_id == "case5":
        history = case5_with_history()
        detector = AmbiguityDetector(_loader)
        amb_with_hist = detector.evaluate(
            report.line_items[0], report.employee, [], history,
        )
        response["case5_with_history"] = {
            "score": amb_with_hist.score,
            "recommendation": amb_with_hist.recommendation,
            "triggered_factors": amb_with_hist.triggered_factors,
            "explanation": amb_with_hist.explanation,
            "llm_review": _json_safe(amb_with_hist.llm_review) if amb_with_hist.llm_review else None,
        }

    return jsonify(response)


@app.route("/api/run_all", methods=["POST"])
def run_all():
    """批量跑全部 7 个场景。"""
    _init()
    results = []
    for preset_id in PRESETS.keys():
        reset_voucher_seq()
        preset = PRESETS[preset_id]
        if preset_id == "case6":
            report, history = case6_pattern_anomaly()
            detector = AmbiguityDetector(_loader)
            amb = detector.evaluate(report.line_items[0], report.employee, [], history)
            results.append({
                "preset_id": preset_id,
                "title": preset["title"],
                "final_status": "detected",
                "total_ms": 0,
                "ambiguity_score": amb.score,
            })
        else:
            report = preset["factory"]()
            result = _ctrl.process_report(report)
            results.append({
                "preset_id": preset_id,
                "title": preset["title"],
                "final_status": result.final_status.value,
                "total_ms": result.total_processing_time.total_seconds() * 1000,
                "amount": report.total_amount,
                "employee_level": report.employee.level.value,
            })
    return jsonify({"results": results})


if __name__ == "__main__":
    print("=" * 60)
    print("  ConcurShield Agent Web UI")
    print("  http://localhost:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)
