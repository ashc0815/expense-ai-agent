/**
 * i18n.js — 双语切换（zh / en）
 *
 * 使用方式：
 *   <script src="/shared/i18n.js"></script>  ← 在 auth.js / nav.js 之前加载
 *
 *   HTML 静态文本:  <span data-i18n="key">中文默认</span>
 *   JS 动态内容:    t('key')
 *   状态/类别:      i18n.status('processing') / i18n.cat('meal')
 *
 * 切换语言:  i18n.setLang('en') / i18n.setLang('zh')
 */
(function (global) {
  "use strict";

  const DICT = {
    // ── Nav ──────────────────────────────────────────────────
    "nav.submit":       { zh: "提交报销",   en: "Submit Expense"   },
    "nav.my-reports":   { zh: "我的报销",   en: "My Reports"       },
    "nav.queue":        { zh: "审批队列",   en: "Approval Queue"   },
    "nav.review":       { zh: "财务复核",   en: "Finance Review"   },
    "nav.export":       { zh: "导出入账",   en: "Export & Post"    },
    "nav.employees":    { zh: "员工档案",   en: "Employees"        },
    "nav.policy":       { zh: "政策管理",   en: "Policy"           },
    "nav.audit-log":    { zh: "审计日志",   en: "Audit Log"        },
    "nav.dashboard":    { zh: "数据统计",   en: "Analytics"        },
    "nav.users":        { zh: "用户管理",   en: "Users"            },

    // ── Roles ────────────────────────────────────────────────
    "role.employee":      { zh: "员工",       en: "Employee"      },
    "role.manager":       { zh: "经理",       en: "Manager"       },
    "role.finance_admin": { zh: "财务管理员", en: "Finance Admin" },

    // ── Status ───────────────────────────────────────────────
    "status.processing":       { zh: "处理中",   en: "Processing"     },
    "status.reviewed":         { zh: "AI 已审",  en: "AI Reviewed"    },
    "status.manager_approved": { zh: "经理已批", en: "Mgr Approved"   },
    "status.finance_approved": { zh: "财务已批", en: "Fin Approved"   },
    "status.exported":         { zh: "已入账",   en: "Posted"         },
    "status.approved":         { zh: "已批准",   en: "Approved"       },
    "status.rejected":         { zh: "已拒绝",   en: "Rejected"       },
    "status.review_failed":    { zh: "审核失败", en: "Review Failed"  },

    // ── Categories ───────────────────────────────────────────
    "cat.meal":          { zh: "餐饮", en: "Meals"         },
    "cat.transport":     { zh: "交通", en: "Transport"     },
    "cat.accommodation": { zh: "住宿", en: "Accommodation" },
    "cat.entertainment": { zh: "招待", en: "Entertainment" },
    "cat.other":         { zh: "其他", en: "Other"         },

    // ── Common buttons / labels ──────────────────────────────
    "btn.sign-out":       { zh: "退出",       en: "Sign Out"  },
    "btn.view":           { zh: "查看",       en: "View"      },
    "btn.new-expense":    { zh: "+ 新建报销", en: "+ New"     },
    "btn.skip-ocr":       { zh: "跳过识别，手动填写", en: "Skip, fill manually" },
    "btn.ocr":            { zh: "OCR 自动识别 →",     en: "Auto OCR →"          },
    "btn.submit-form":    { zh: "提交报销单",          en: "Submit Expense"      },
    "btn.approve":        { zh: "通过并生成凭证 (A)",  en: "Approve (A)"         },
    "btn.reject":         { zh: "退回 (R)",            en: "Reject (R)"          },
    "btn.bulk-approve":   { zh: "批量通过低风险",      en: "Bulk Approve Low-Risk" },
    "btn.cancel":         { zh: "取消",               en: "Cancel"                },
    "btn.save":           { zh: "保存",               en: "Save"                  },
    "btn.edit":           { zh: "编辑",               en: "Edit"                  },
    "btn.delete":         { zh: "删除",               en: "Delete"                },

    // ── AI panel ─────────────────────────────────────────────
    "ai.title":      { zh: "AI 报销助手",             en: "AI Expense Assistant"       },
    "ai.chat-tab":   { zh: "对话",                    en: "Chat"                        },
    "ai.detail-tab": { zh: "详情",                    en: "Details"                     },
    "ai.empty-detail": { zh: "点击列表中的一行查看详情", en: "Click a row to view details" },
    "ai.welcome-qa": {
      zh: "你好，我是报销助手 👋\n\n试试问我：\n• 我这个月花了多少？\n• 本季度出差费？\n• 最近有哪些报销记录？",
      en: "Hi, I'm your expense assistant 👋\n\nTry asking:\n• How much did I spend this month?\n• Q3 travel expenses?\n• My recent expenses?"
    },
    "ai.welcome-submit": {
      zh: "你好，我是报销助手 👋\n把发票拖到左侧或告诉我要报销什么，我帮你自动填写字段。",
      en: "Hi, I'm your expense assistant 👋\n\nDrag a receipt to the left, or tell me what to expense — I'll fill in the fields automatically."
    },
    "ai.placeholder-submit": { zh: "告诉我怎么帮你…",        en: "Tell me how I can help…"               },
    "ai.placeholder-qa":     { zh: "问我：这个月花了多少？", en: "Ask: How much did I spend this month?" },
    "detail.note":      { zh: "备注",           en: "Note"               },
    "detail.view-full": { zh: "查看完整详情 →", en: "View Full Details →" },

    // ── My Reports page ──────────────────────────────────────
    "reports.title":       { zh: "我的报销单",   en: "My Expenses"   },
    "reports.all-status":  { zh: "全部状态",     en: "All Status"    },
    "reports.col-date":    { zh: "日期",         en: "Date"          },
    "reports.col-merchant":{ zh: "商户",         en: "Merchant"      },
    "reports.col-amount":  { zh: "金额",         en: "Amount"        },
    "reports.col-category":{ zh: "类别",         en: "Category"      },
    "reports.col-status":  { zh: "状态",         en: "Status"        },
    "reports.col-risk":    { zh: "风险级别",     en: "Risk"          },
    "reports.col-action":  { zh: "操作",         en: "Action"        },

    // ── Submit page ──────────────────────────────────────────
    "submit.step1":        { zh: "上传发票",   en: "Upload"          },
    "submit.step2":        { zh: "确认识别",   en: "Confirm OCR"     },
    "submit.step3":        { zh: "补充信息",   en: "Details"         },
    "submit.upload-title": { zh: "上传发票图片",        en: "Upload Receipt"          },
    "submit.upload-hint":  { zh: "点击或拖放发票到此处", en: "Click or drag receipt here" },
    "submit.upload-sub":   { zh: "支持 JPG · PNG · WEBP · PDF，最大 10 MB",
                             en: "JPG · PNG · WEBP · PDF, max 10 MB" },
    "submit.confirm-title":{ zh: "确认识别结果",  en: "Confirm OCR Results" },
    "submit.ocr-legend":   { zh: "绿色 = 高置信 · 黄色 = 请确认 · 红色 = 未识别",
                             en: "Green = high confidence · Yellow = review · Red = not detected" },
    "submit.details-title":{ zh: "补充信息", en: "Expense Details" },
    "submit.merchant":     { zh: "商户名称 *",    en: "Merchant *"    },
    "submit.amount":       { zh: "金额 *",        en: "Amount *"      },
    "submit.currency":     { zh: "货币",          en: "Currency"      },
    "submit.category":     { zh: "费用类别 *",    en: "Category *"    },
    "submit.date":         { zh: "消费日期 *",    en: "Date *"        },
    "submit.tax":          { zh: "税额",          en: "Tax Amount"    },
    "submit.project":      { zh: "项目",          en: "Project"       },
    "submit.invoice-no":   { zh: "发票号码",      en: "Invoice No."   },
    "submit.invoice-code": { zh: "发票代码",      en: "Invoice Code"  },
    "submit.notes":        { zh: "备注说明",      en: "Notes"         },

    // ── Finance Review page ──────────────────────────────────
    "review.title":        { zh: "财务复核队列",           en: "Finance Review Queue"                     },
    "review.subtitle":     { zh: "经理已批准、待财务审核入账", en: "Manager approved, pending finance posting" },
    "stats.total-amount":  { zh: "待审合计金额",  en: "Total Pending"   },
    "stats.avg-risk":      { zh: "平均风险评分",  en: "Avg Risk Score"  },
    "stats.t4-count":      { zh: "高风险单 (T4)", en: "High Risk (T4)"  },
    "stats.tier-dist":     { zh: "风险分布",      en: "Risk Distribution" },

    // ── Finance Review page (extended) ──────────────────────────────────────
    "review.placeholder":      { zh: "请从左侧选择一张报销单",  en: "Select an expense from the list"          },
    "review.load-fail":        { zh: "加载失败：",              en: "Load failed: "                            },
    "review.pending-suffix":   { zh: "单待复核",               en: "pending review"                           },
    "review.empty":            { zh: "🎉 没有待复核的报销单",   en: "🎉 No expenses pending review"            },
    "review.all-done":         { zh: "全部已处理",              en: "All done"                                 },
    "review.mgr-approved-at":  { zh: "经理批于",               en: "Mgr approved"                             },
    "review.unknown-dept":     { zh: "未知部门",               en: "Unknown dept"                             },
    "review.view-receipt":     { zh: "查看发票",               en: "View Receipt"                             },
    "review.risk-label":       { zh: "AI 风险评分",            en: "AI Risk Score"                            },
    "review.mgr-approval":     { zh: "经理审批",               en: "Manager Approval"                         },
    "review.accounting-codes": { zh: "入账编码（可调整）",     en: "Accounting Codes (editable)"              },
    "review.field-gl":         { zh: "会计科目 (GL)",          en: "GL Account"                               },
    "review.field-cc":         { zh: "成本中心",               en: "Cost Center"                              },
    "review.field-proj":       { zh: "项目编号",               en: "Project"                                  },
    "review.no-project":       { zh: "— 不指派 —",            en: "— None —"                                 },
    "review.invoice-info":     { zh: "发票信息",               en: "Invoice Details"                          },
    "review.invoice-no":       { zh: "发票号码：",             en: "Invoice No:"                              },
    "review.invoice-code":     { zh: "发票代码：",             en: "Invoice Code:"                            },
    "review.seller-tax":       { zh: "销方税号：",             en: "Seller Tax ID:"                           },
    "review.tax-amount":       { zh: "税额：",                 en: "Tax Amount:"                              },
    "review.notes-title":      { zh: "备注",                   en: "Notes"                                    },
    "review.finance-comment":  { zh: "财务意见（可选）",       en: "Finance Comment (optional)"               },
    "review.comment-ph":       { zh: "留给员工和审计的备注",   en: "Notes for employee and audit trail"       },
    "review.t4-count-val":     { zh: "{n} 单",                 en: "{n}"                                      },
    "review.t4-none":          { zh: "无",                     en: "None"                                     },
    "review.approve-ok":       { zh: "✓ 已通过，凭证号：",    en: "✓ Approved, voucher: "                    },
    "review.approve-fail":     { zh: "通过失败：",             en: "Approval failed: "                        },
    "review.bulk-no-low":      { zh: "当前队列没有 T1/T2 低风险单据", en: "No T1/T2 low-risk expenses in queue" },
    "review.bulk-confirm":     { zh: "批量通过 {n} 单低风险报销（合计 ¥{total}）？\n\n系统将为每单自动生成凭证号，不会覆盖 GL/成本中心/项目。",
                                 en: "Bulk approve {n} low-risk expenses (total ¥{total})?\n\nA voucher will be generated for each — GL / Cost Center / Project will not be overwritten." },
    "review.bulk-processing":  { zh: "处理中…",               en: "Processing…"                              },
    "review.bulk-ok":          { zh: "✓ 已通过 {approved} 单，跳过 {skipped}", en: "✓ Approved {approved}, skipped {skipped}" },
    "review.bulk-fail":        { zh: "批量通过失败：",         en: "Bulk approval failed: "                   },
    "review.reject-prompt":    { zh: "请说明拒绝原因",         en: "Please provide a reason for rejection"    },
    "review.reject-fail":      { zh: "拒绝失败：",             en: "Rejection failed: "                       },

    // ── Manager Queue ────────────────────────────────────────
    "queue.title":    { zh: "审批队列",     en: "Approval Queue"       },
    "queue.subtitle": { zh: "等待您审批的报销单", en: "Expenses awaiting your approval" },

    // ── Export page ──────────────────────────────────────────
    "export.title":      { zh: "ERP 入账导出", en: "ERP Export"                },
    "export.subtitle":   { zh: "财务已批准、未导出的报销单 — 选择后导出 CSV 即可推入金蝶/用友/SAP",
                           en: "Finance-approved, unposted expenses — export CSV to push to ERP (SAP/NetSuite/QuickBooks)" },
    "export.btn":        { zh: "导出选中",   en: "Export Selected"            },
    "export.empty":      { zh: "没有待导出的报销单", en: "No expenses pending export" },
    "export.col-voucher":{ zh: "凭证号",     en: "Voucher"                    },
    "export.col-emp":    { zh: "员工",       en: "Employee"                   },
    "export.col-dept":   { zh: "部门",       en: "Department"                 },
    "export.col-merchant":{ zh: "商户",      en: "Merchant"                   },
    "export.col-cat":    { zh: "类别",       en: "Category"                   },
    "export.col-gl":     { zh: "GL 科目",    en: "GL Account"                 },
    "export.col-proj":   { zh: "项目",       en: "Project"                    },
    "export.col-amount": { zh: "金额",       en: "Amount"                     },
    "export.col-fin-at": { zh: "财务审批于", en: "Finance Approved"           },
    "export.csv-hint":   { zh: "CSV 列：凭证号 / 业务日期 / 员工 / 部门 / 成本中心 / GL 科目 / 项目 / 类别 / 商户 / 金额 / 税额 / 发票代码 / 发票号 / 销方税号 / 摘要 / 经理审批 / 财务审批",
                           en: "CSV columns: Voucher / Date / Employee / Dept / Cost Center / GL / Project / Category / Merchant / Amount / Tax / Invoice Code / Invoice No / Tax ID / Description / Manager Approval / Finance Approval" },

    // ── Employees page ───────────────────────────────────────────────────────
    "emp.title":            { zh: "员工档案",     en: "Employees"               },
    "emp.subtitle":         { zh: "部门 / 成本中心 / 银行账号 — 提交报销时自动派生到入账字段",
                              en: "Department / Cost Center / Bank Account — auto-populated on expense submission" },
    "emp.new-btn":          { zh: "+ 新建员工",   en: "+ New Employee"           },
    "emp.modal-title-new":  { zh: "新建员工",     en: "New Employee"             },
    "emp.modal-title-edit": { zh: "编辑员工",     en: "Edit Employee"            },
    "emp.field-id":         { zh: "工号 *",       en: "Employee ID *"            },
    "emp.field-name":       { zh: "姓名 *",       en: "Name *"                   },
    "emp.field-email":      { zh: "邮箱",         en: "Email"                    },
    "emp.field-level":      { zh: "职级",         en: "Level"                    },
    "emp.field-dept":       { zh: "部门 *",       en: "Department *"             },
    "emp.field-cc":         { zh: "成本中心 *",   en: "Cost Center *"            },
    "emp.field-city":       { zh: "城市",         en: "City"                     },
    "emp.field-mgr":        { zh: "直属经理工号", en: "Manager ID"               },
    "emp.field-bank":       { zh: "银行账号",     en: "Bank Account"             },
    "emp.col-id":           { zh: "工号",         en: "Employee ID"              },
    "emp.col-name":         { zh: "姓名",         en: "Name"                     },
    "emp.col-dept":         { zh: "部门",         en: "Department"               },
    "emp.col-cc":           { zh: "成本中心",     en: "Cost Center"              },
    "emp.col-level":        { zh: "职级",         en: "Level"                    },
    "emp.col-email":        { zh: "邮箱",         en: "Email"                    },
    "emp.col-mgr":          { zh: "直属经理",     en: "Manager"                  },
    "emp.col-action":       { zh: "操作",         en: "Actions"                  },
    "emp.empty":            { zh: "暂无员工档案，请点击右上角\"+ 新建员工\"",
                              en: "No employees found. Click \"+ New Employee\" to add one." },
    "emp.load-fail":        { zh: "加载失败：",   en: "Load failed: "            },
    "emp.confirm-del":      { zh: "确认删除员工", en: "Confirm delete employee"  },
    "emp.save-fail":        { zh: "保存失败：",   en: "Save failed: "            },
    "emp.del-fail":         { zh: "删除失败：",   en: "Delete failed: "          },

    // ── Dashboard page ───────────────────────────────────────────────────────
    "dash.title":             { zh: "报销概览",         en: "Expense Overview"            },
    "dash.subtitle":          { zh: "实时数据 · 今日更新", en: "Live data · Updated today"  },
    "dash.export-btn":        { zh: "↓ 导出 CSV",       en: "↓ Export CSV"                },
    "dash.loading":           { zh: "加载中…",           en: "Loading…"                    },
    "dash.status-chart":      { zh: "按状态分布",        en: "By Status"                   },
    "dash.tier-chart":        { zh: "AI 风险等级分布",   en: "AI Risk Tier Distribution"   },
    "dash.tier.t1":           { zh: "T1 — 低风险",       en: "T1 — Low Risk"               },
    "dash.tier.t2":           { zh: "T2 — 中低风险",     en: "T2 — Medium-Low"             },
    "dash.tier.t3":           { zh: "T3 — 中高风险",     en: "T3 — Medium-High"            },
    "dash.tier.t4":           { zh: "T4 — 高风险",       en: "T4 — High Risk"              },
    "dash.kpi.total":         { zh: "报销单总数",        en: "Total Expenses"              },
    "dash.kpi.total-amount":  { zh: "累计金额",          en: "Total Amount"                },
    "dash.kpi.pending-mgr":   { zh: "待经理审批",        en: "Pending Manager"             },
    "dash.kpi.pending-finance":{ zh: "待财务复核",       en: "Pending Finance"             },
    "dash.kpi.pending-export":{ zh: "待导出入账",        en: "Pending Export"              },
    "dash.kpi.avg-risk":      { zh: "平均风险分",        en: "Avg Risk Score"              },
    "dash.load-fail":         { zh: "加载失败：",        en: "Load failed: "               },
    "dash.no-data":           { zh: "暂无数据",          en: "No data"                     },

    // ── Audit Log page ───────────────────────────────────────────────────────
    "audit.title":               { zh: "审计日志",       en: "Audit Log"          },
    "audit.action-all":          { zh: "全部操作",       en: "All Actions"        },
    "audit.btn-search":          { zh: "查询",           en: "Search"             },
    "audit.col-time":            { zh: "时间",           en: "Time"               },
    "audit.col-actor":           { zh: "操作人",         en: "Actor"              },
    "audit.col-action":          { zh: "操作",           en: "Action"             },
    "audit.col-resource":        { zh: "资源类型",       en: "Resource Type"      },
    "audit.col-resource-id":     { zh: "资源 ID",        en: "Resource ID"        },
    "audit.col-detail":          { zh: "详情",           en: "Detail"             },
    "audit.empty":               { zh: "暂无日志",       en: "No log entries"     },
    "audit.load-fail":           { zh: "加载失败：",     en: "Load failed: "      },
    "audit.action.created":      { zh: "提交报销",       en: "Expense Submitted"  },
    "audit.action.approved":     { zh: "批准报销",       en: "Expense Approved"   },
    "audit.action.rejected":     { zh: "拒绝报销",       en: "Expense Rejected"   },
    "audit.action.ai-complete":  { zh: "AI 审核完成",    en: "AI Review Complete" },
    "audit.action.ai-failed":    { zh: "AI 审核失败",    en: "AI Review Failed"   },

    // ── Report Detail page ───────────────────────────────────────────────────
    "detail.back":              { zh: "← 返回列表",    en: "← Back to list"                        },
    "detail.title":             { zh: "报销详情",       en: "Expense Detail"                         },
    "detail.loading":           { zh: "加载中…",        en: "Loading…"                               },
    "detail.no-id":             { zh: "缺少报销单 ID",  en: "Missing expense ID"                     },
    "detail.forbidden":         { zh: "无权查看该报销单", en: "You don't have access to this expense" },
    "detail.not-found":         { zh: "报销单不存在",   en: "Expense not found"                      },
    "detail.voucher-prefix":    { zh: "凭证号",         en: "Voucher"                                },
    "detail.ai-reviewing":      { zh: "AI 审核中…",     en: "AI reviewing…"                          },
    "detail.flow-title":        { zh: "流程进度",        en: "Progress"                               },
    "detail.flow.submitted":    { zh: "提交",            en: "Submitted"                              },
    "detail.flow.reviewed":     { zh: "AI 审核",         en: "AI Review"                              },
    "detail.flow.mgr":          { zh: "经理批准",        en: "Mgr Approved"                           },
    "detail.flow.fin":          { zh: "财务批准",        en: "Fin Approved"                           },
    "detail.flow.exported":     { zh: "已入账",          en: "Posted"                                 },
    "detail.info-title":        { zh: "报销信息",        en: "Expense Info"                           },
    "detail.receipt-alt":       { zh: "发票",            en: "Receipt"                                },
    "detail.field-merchant":    { zh: "商户",            en: "Merchant"                               },
    "detail.field-amount":      { zh: "金额",            en: "Amount"                                 },
    "detail.field-date":        { zh: "日期",            en: "Date"                                   },
    "detail.field-category":    { zh: "类别",            en: "Category"                               },
    "detail.field-tax":         { zh: "税额",            en: "Tax"                                    },
    "detail.field-project":     { zh: "项目",            en: "Project"                                },
    "detail.notes-prefix":      { zh: "备注：",          en: "Notes: "                                },
    "detail.accounting-title":  { zh: "入账信息",        en: "Accounting Info"                        },
    "detail.field-dept":        { zh: "部门",            en: "Department"                             },
    "detail.field-cc":          { zh: "成本中心",        en: "Cost Center"                            },
    "detail.field-gl":          { zh: "会计科目 (GL)",   en: "GL Account"                             },
    "detail.field-voucher":     { zh: "凭证号",          en: "Voucher No."                            },
    "detail.field-inv-no":      { zh: "发票号码",        en: "Invoice No."                            },
    "detail.field-inv-code":    { zh: "发票代码",        en: "Invoice Code"                           },
    "detail.field-exported-at": { zh: "入账时间",        en: "Posted At"                              },
    "detail.mgr-title":         { zh: "经理审批",        en: "Manager Approval"                       },
    "detail.fin-title":         { zh: "财务审批",        en: "Finance Approval"                       },
    "detail.field-approver":    { zh: "审批人",          en: "Approver"                               },
    "detail.field-time":        { zh: "时间",            en: "Time"                                   },
    "detail.comment-prefix":    { zh: "意见：",          en: "Comment: "                              },
    "detail.ai-title":          { zh: "AI 审核报告",     en: "AI Review Report"                       },
    "detail.risk-label":        { zh: "综合风险评分",    en: "Overall Risk Score"                     },
    "detail.step-skipped":      { zh: "已跳过",          en: "Skipped"                                },
    "detail.step-passed":       { zh: "通过",            en: "Passed"                                 },
    "detail.step-failed":       { zh: "未通过",          en: "Failed"                                 },
    "detail.skill.0":           { zh: "收据验证",        en: "Receipt Check"                          },
    "detail.skill.1":           { zh: "额度审批",        en: "Amount Review"                          },
    "detail.skill.2":           { zh: "合规检查",        en: "Policy Check"                           },
    "detail.skill.3":           { zh: "凭证生成",        en: "Voucher Gen"                            },
    "detail.skill.4":           { zh: "支付处理",        en: "Payment"                                },

    // ── my-reports static ────────────────────────────────────
    "reports.page-title": { zh: "我的报销单", en: "My Expenses"   },
    "reports.new-btn":    { zh: "+ 新建报销", en: "+ New Expense"  },
    "reports.empty":      { zh: "暂无报销单", en: "No expenses found" },

    // ── budget ──────────────────────────────────────────────────────
    'budget.info':        { zh: '团队预算已用 {pct}%，本次提交后预计达 {proj}%', en: 'Team budget {pct}% used — this submission will bring it to {proj}%' },
    'budget.blocked':     { zh: '预算阈值已达，报销将被暂挂等待财务管理员审批', en: 'Budget threshold reached — submission will be held for finance admin review' },
    'budget.over-warn':   { zh: '团队预算已超出，报销将继续提交，财务管理员将收到通知', en: 'Team budget exceeded — submission will proceed but finance admin will be notified' },
    'budget.held-badge':  { zh: '预算暂挂', en: 'Budget Held' },
    'budget.unblock-btn': { zh: '解锁', en: 'Unblock' },
    'budget.unblock-confirm': { zh: '确认解锁该报销单？将重新进入财务审核队列。', en: 'Unblock this submission? It will re-enter the finance review queue.' },
    'budget.policy-title':{ zh: '预算策略配置', en: 'Budget Policy' },
    'budget.period-label':{ zh: '期间', en: 'Period' },
    'budget.col-cc':      { zh: '成本中心', en: 'Cost Center' },
    'budget.col-total':   { zh: '预算总额', en: 'Total Budget' },
    'budget.col-used':    { zh: '已用', en: 'Used' },
    'budget.col-warn':    { zh: '提示阈值', en: 'Info at' },
    'budget.col-block':   { zh: '拦截阈值', en: 'Block at' },
    'budget.col-overbudget':{ zh: '超限行为', en: 'Over 100%' },
    'budget.action-warn': { zh: '仅提示', en: 'Warn only' },
    'budget.action-block':{ zh: '拦截', en: 'Block' },
    'budget.global-default':{ zh: '全局默认', en: 'Global default' },
    'budget.add-cc-btn':  { zh: '+ 添加成本中心', en: '+ Add Cost Center' },
    'budget.save-btn':    { zh: '保存', en: 'Save' },
    'budget.edit-btn':    { zh: '编辑', en: 'Edit' },
    'budget.loading':     { zh: '加载预算数据…', en: 'Loading budget data…' },
    'budget.no-data':     { zh: '暂无预算配置', en: 'No budget configured' },
  };

  // ── Core ─────────────────────────────────────────────────────────
  const _lang = localStorage.getItem("cs-lang") || "zh";

  function t(key) {
    const entry = DICT[key];
    if (!entry) return key;
    return entry[_lang] || entry.zh || key;
  }

  function setLang(lang) {
    localStorage.setItem("cs-lang", lang);
    location.reload();
  }

  // Status / Category helpers (used by page JS)
  const _STATUS = {
    processing: "status.processing", reviewed: "status.reviewed",
    manager_approved: "status.manager_approved", finance_approved: "status.finance_approved",
    exported: "status.exported", approved: "status.approved",
    rejected: "status.rejected", review_failed: "status.review_failed",
  };
  const _CAT = {
    meal: "cat.meal", transport: "cat.transport", accommodation: "cat.accommodation",
    entertainment: "cat.entertainment", other: "cat.other",
  };

  function status(s) { return _STATUS[s] ? t(_STATUS[s]) : (s || "—"); }
  function cat(c)    { return _CAT[c]    ? t(_CAT[c])    : (c || "—"); }

  // Apply data-i18n attributes to DOM
  function apply() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.dataset.i18n;
      const val = t(key);
      if (val !== key) el.textContent = val;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }

  global.LANG  = _lang;
  global.t     = t;
  global.i18n  = { lang: _lang, setLang, t, status, cat, apply };
})(window);
