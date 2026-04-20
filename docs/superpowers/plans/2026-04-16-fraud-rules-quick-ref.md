# Fraud Rules 11-20 — Quick Reference Card

> One-page summary. Full specs: `fraud-level2-llm-extraction.md` and `fraud-level4-agent-reasoning.md`

## All Rules at a Glance

| # | Rule Name | Level | Score | Key Input | Detection Essence |
|---|-----------|-------|-------|-----------|-------------------|
| 11 | `description_template` | L2 | 65 | LLM `template_score` ≥ 70 | 备注模板化填写 |
| 12 | `receipt_contradiction` | L2 | 70 | LLM `contradiction_found` | Receipt 与备注地点矛盾 |
| 13 | `person_amount_mismatch` | L2 | 60 | LLM `person_amount_reasonable=False` | 人均消费异常高 |
| 14 | `vague_description` | L2 | 60 | LLM `vagueness_score` ≥ 60 + suspicious category | 模糊事由掩盖消费 |
| 15 | `collusion_pattern` | L4 | 75 | Cross-employee merchant data | A/B 轮流报销拆单 |
| 16 | `rationalized_personal` | L4 | 70 | Submission batch | 周末度假村多类别报销 |
| 17 | `vendor_frequency` | L4 | 65 | Employee history | 单一商户频繁报销 |
| 18 | `seasonal_anomaly` | L4 | 60 | Quarterly totals (DB) | 季度金额突增 |
| 19 | `approver_collusion` | L4 | 70 | ApprovalRow records | 审批人对特定人极快通过 |
| 20 | `ghost_employee` | L4 | 90 | EmployeeRow + submissions | 离职后仍有报销 |

## Dependency Graph

```
store.py                    fraud_rules.py              skill_fraud_check.py
────────                    ──────────────              ────────────────────
list_recent_descriptions ──▶ (feeds LLM analyzer) ──▶ process_report_async
                                                        │
                            rule_description_template ──┤ L2 (per submission)
                            rule_receipt_contradiction──┤
                            rule_person_amount_mismatch─┤
                            rule_vague_description ─────┤
                                                        │
list_submissions_by_merchant                            │
list_approvals_by_approver   rule_collusion_pattern ────┤ L4 (per report)
list_employee_submissions    rule_rationalized_personal─┤
  _by_quarter                rule_vendor_frequency ─────┤
                            rule_seasonal_anomaly ──────┤
                            rule_approver_collusion ────┤
                            rule_ghost_employee ────────┘
```

## Config Keys Added (all in DEFAULT_CONFIG)

```python
# Level 2
"template_score_threshold": 70,
"vagueness_threshold": 60,
"vagueness_suspicious_categories": ["gift", "entertainment", "supplies", "other"],

# Level 4
"collusion_min_pair_count": 3,
"vendor_frequency_threshold": 6,
"seasonal_spike_multiplier": 2.5,
"approver_speed_ratio": 3.0,
"approver_min_samples": 3,
```

## Test Count

| Area | Tests |
|------|-------|
| LLM analyzer | 4 |
| Rules 11-14 | 14 |
| Rules 15-20 | 18 |
| Integration | 4 |
| **Total new** | **40** |
