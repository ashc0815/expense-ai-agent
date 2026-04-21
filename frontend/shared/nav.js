/**
 * nav.js — Dynamic navigation injection + role-based menu.
 * 依赖 i18n.js（需在本文件之前加载）。
 */
(function (global) {
  "use strict";

  const NAV_ITEMS = [
    { key: "nav.submit",     href: "/employee/quick.html",      roles: ["employee"]      },
    { key: "nav.my-reports", href: "/employee/my-reports.html", roles: ["employee"]      },
    { key: "nav.queue",      href: "/manager/queue.html",       roles: ["manager"]       },
    { key: "nav.review",     href: "/finance/review.html",      roles: ["finance_admin"] },
    { key: "nav.export",     href: "/finance/export.html",      roles: ["finance_admin"] },
    { key: "nav.employees",  href: "/admin/employees.html",     roles: ["finance_admin"] },
    { key: "nav.policy",     href: "/admin/policy.html",        roles: ["finance_admin"] },
    { key: "nav.audit-log",  href: "/admin/audit-log.html",     roles: ["finance_admin"] },
    { key: "nav.dashboard",  href: "/admin/dashboard.html",     roles: ["finance_admin"] },
    { key: "nav.users",      href: "/admin/users.html",         roles: ["finance_admin"] },
  ];

  const ROLE_KEYS = {
    employee:      "role.employee",
    manager:       "role.manager",
    finance_admin: "role.finance_admin",
  };

  function _currentPage() {
    return location.pathname.split("/").pop();
  }

  function _buildNav(user) {
    // Resolve _t lazily at render time so i18n.js is guaranteed to have run
    const _t      = global.t || (k => k);
    const curLang = (global.i18n && global.i18n.lang) || "zh";
    const isEn    = curLang === "en";

    const userRoles = user.roles || [user.role || "employee"];
    const visible = NAV_ITEMS.filter(
      item => !item.roles || item.roles.some(r => userRoles.includes(r))
    );
    const links = visible.map(item => {
      const active = _currentPage() === item.href.split("/").pop();
      return `<a href="${item.href}" class="nav-link${active ? " active" : ""}">
        ${_t(item.key)}</a>`;
    }).join("");
    const saved = localStorage.getItem("mock_role") || "employee";

    return `
<nav class="top-nav">
  <div class="nav-brand">
    <div class="brand-dot"></div>
    ExpenseFlow
  </div>
  <div class="nav-links">${links}</div>
  <div class="nav-user">
    <select class="role-switcher" onchange="auth.setMockRole(this.value); location.reload();"
            title="${isEn ? "Dev: switch role" : "开发模式：切换角色"}"
            style="padding:.3rem .5rem; border:1px solid #d1d5db; border-radius:4px; font-size:.8rem; background:white; cursor:pointer">
      <option value="employee"      ${saved === "employee"      ? "selected" : ""}>👤 ${_t("role.employee")}</option>
      <option value="manager"       ${saved === "manager"       ? "selected" : ""}>👔 ${_t("role.manager")}</option>
      <option value="finance_admin" ${saved === "finance_admin" ? "selected" : ""}>💼 ${_t("role.finance_admin")}</option>
    </select>
    <button
      onclick="i18n.setLang('${isEn ? "zh" : "en"}')"
      title="${isEn ? "切换到中文" : "Switch to English"}"
      style="padding:.3rem .5rem; border:1px solid #d1d5db; border-radius:4px; font-size:.8rem; background:white; cursor:pointer; color:#374151">
      🌐 ${isEn ? "中" : "EN"}
    </button>
    <button class="btn-signout" onclick="auth.signOut()">${_t("btn.sign-out")}</button>
  </div>
</nav>`;
  }

  function _injectNav() {
    const root = document.getElementById("nav-root");
    if (!root) return;
    const user = global.auth.getUser();
    if (!user) return;
    root.innerHTML = _buildNav(user);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _injectNav);
  } else {
    _injectNav();
  }

  global.nav = { refresh: _injectNav };
})(window);
