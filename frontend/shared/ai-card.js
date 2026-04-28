/**
 * ai-card.js — 嵌入式 AI 解释卡组件
 *
 * 用法：
 *   <link rel="stylesheet" href="/shared/ai-card.css"> （或者直接复用页面 CSS）
 *   <script src="/shared/ai-card.js"></script>
 *
 *   // 在审批页 detail 面板里插入 placeholder：
 *   <div id="ai-card-{submission_id}"></div>
 *
 *   // 然后调用：
 *   await aiCard.load(submission_id);
 *
 * 设计原则（这是"第三种 agent 形态"）：
 *   - 不是 chat，是单向输出
 *   - 经理点开报销 → 卡已经在那 → 10 秒做决策
 *   - 给推荐不给决定（按钮永远在人手里）
 */
(function (global) {
  "use strict";

  function tierClass(tier) {
    return { T1: "tier-low", T2: "tier-low",
             T3: "tier-mid", T4: "tier-high" }[tier] || "tier-mid";
  }
  function tierLabel(tier) {
    return { T1: "低风险", T2: "次低风险",
             T3: "中风险", T4: "高风险" }[tier] || tier;
  }
  function recIcon(rec) {
    return { approve: "✅", review: "🔍", reject: "🛑" }[rec] || "ℹ️";
  }
  function escape(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderLoading(container) {
    container.innerHTML = `
      <div class="ai-card ai-card-loading">
        <div class="ai-card-shimmer"></div>
        <div class="ai-card-meta">AI 正在汇总审计数据…</div>
      </div>`;
  }

  function renderError(container, msg) {
    container.innerHTML = `
      <div class="ai-card ai-card-error">
        <div class="ai-card-meta">⚠ AI 解释卡加载失败：${escape(msg)}</div>
      </div>`;
  }

  function render(container, data) {
    const flagsHtml = (label, items, cls) => {
      if (!items || !items.length) return "";
      return `
        <div class="ai-flag-group ${cls}">
          <div class="ai-flag-label">${label}</div>
          <ul class="ai-flag-list">
            ${items.map(t => `<li>${escape(t)}</li>`).join("")}
          </ul>
        </div>`;
    };

    // ── Cite the rule: structured violations rendering ──
    // Each violation has {rule_id, rule_text, severity, suggestion?, evidence?}
    // Source: audit_report.violations, populated by the 5-Skill pipeline +
    // agent.violation_registry. This is what makes the AI's flagging
    // auditable — every red/yellow flag points to a specific, citable rule.
    const violationsHtml = (() => {
      const vs = data.violations || [];
      if (!vs.length) return "";
      const sevClass = (sev) => ({
        error: "ai-vio-error",
        warn: "ai-vio-warn",
        info: "ai-vio-info",
      }[sev] || "ai-vio-warn");
      const sevIcon = (sev) => ({ error: "🛑", warn: "⚠️", info: "ℹ️" }[sev] || "•");
      return `
        <div class="ai-violations">
          <div class="ai-violations-label">📋 触发规则 (${vs.length})</div>
          ${vs.map(v => `
            <div class="ai-vio ${sevClass(v.severity)}">
              <div class="ai-vio-head">
                <span class="ai-vio-icon">${sevIcon(v.severity)}</span>
                <code class="ai-vio-id">${escape(v.rule_id || "")}</code>
              </div>
              <div class="ai-vio-text">${escape(v.rule_text || "")}</div>
              ${v.suggestion ? `
                <div class="ai-vio-suggestion">建议：${escape(v.suggestion)}</div>` : ""}
              ${v.evidence ? `
                <div class="ai-vio-evidence">证据：<code>${escape(v.evidence)}</code></div>` : ""}
            </div>
          `).join("")}
        </div>`;
    })();

    const ctxLine = data.context && data.context.history_avg != null
      ? `员工历史 ${data.context.history_count} 笔 · 平均 ¥${data.context.history_avg.toFixed(0)}`
      : (data.context && data.context.history_count
          ? `员工历史 ${data.context.history_count} 笔`
          : `员工尚无历史报销`);

    container.innerHTML = `
      <div class="ai-card">
        <div class="ai-card-header">
          <div class="ai-card-title">
            <span class="ai-card-icon">💡</span>
            <span>AI 审核摘要</span>
          </div>
          <div class="ai-card-tier ${tierClass(data.tier)}">
            ${escape(data.tier)} · ${tierLabel(data.tier)}
          </div>
        </div>

        <div class="ai-card-headline">
          ${recIcon(data.recommendation)} <strong>${escape(data.headline)}</strong>
          <span class="ai-card-meta-inline">风险分 ${data.risk_score?.toFixed(0) ?? "—"}/100</span>
        </div>

        <div class="ai-card-context">${escape(ctxLine)}</div>

        ${flagsHtml("✓ 通过", data.green_flags, "ai-green")}
        ${flagsHtml("⚠ 注意", data.yellow_flags, "ai-yellow")}
        ${flagsHtml("✗ 风险", data.red_flags, "ai-red")}
        ${violationsHtml}

        ${data.advisory ? `
          <div class="ai-card-advisory">
            <span class="ai-advisory-label">建议</span>
            <span>${escape(data.advisory)}</span>
          </div>` : ""}

        ${(global.auth && global.auth.isDev()) ? `
        <div class="ai-card-footer">
          <span title="${(data._tools_called || []).join(', ')}">
            🔧 调用了 ${(data._tools_called || []).length} 个只读 tool
          </span>
          <span class="ai-card-role">role: ${escape(data._agent_role || "")}</span>
        </div>` : ""}
      </div>`;
  }

  async function load(submissionId, containerOverride) {
    const container = containerOverride || document.getElementById(`ai-card-${submissionId}`);
    if (!container) return;
    renderLoading(container);
    try {
      const data = await global.api.getExplanation(submissionId);
      render(container, data);
    } catch (err) {
      renderError(container, err.message || "未知错误");
    }
  }

  global.aiCard = { load, render };
})(window);
