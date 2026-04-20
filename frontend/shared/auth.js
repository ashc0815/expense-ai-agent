/**
 * auth.js — Mock / Clerk dual-mode auth helper
 *
 * AUTH_MODE is set via <script> before loading this file:
 *   window.AUTH_MODE = "mock" | "clerk"
 *
 * Exported surface:
 *   auth.getHeaders()  → { "X-User-Id": ..., "X-User-Role": ... }  (mock)
 *                     OR { "Authorization": "Bearer <jwt>" }        (clerk)
 *   auth.getUser()    → { id, role, name }
 *   auth.signOut()
 */
(function (global) {
  "use strict";

  const MODE = (global.AUTH_MODE || "mock").toLowerCase();

  // ── Mock mode ──────────────────────────────────────────────────
  const MOCK_USERS = {
    employee:      { id: "emp-dev",  roles: ["employee"],                    name: "Dev Employee" },
    manager:       { id: "mgr-dev",  roles: ["employee", "manager"],         name: "Dev Manager" },
    finance_admin: { id: "fin-dev",  roles: ["employee", "finance_admin"],   name: "Dev Finance Admin" },
  };

  // Dev 体验：支持 URL 参数 ?as=manager 切角色，?dev=1 开启开发者面板
  // 使用 localStorage（跨 tab 持久化）替代 sessionStorage（按 tab 隔离）。
  (function _bootstrap() {
    if (MODE !== "mock") return;
    const params = new URLSearchParams(location.search);

    // ── ?as=ROLE 角色切换 ──
    const urlRole = params.get("as");
    if (urlRole && MOCK_USERS[urlRole]) {
      localStorage.setItem("mock_role", urlRole);
    }

    // ── ?dev=1 / ?dev=0 开/关 dev mode ──
    // 按观众分层的核心机制：dev mode 决定是否给当前用户看 internal 信息
    // (tool 调用、agent_role、phase 标签、审计 trace 等)。
    const devParam = params.get("dev");
    if (devParam === "1") localStorage.setItem("dev_mode", "1");
    if (devParam === "0") localStorage.removeItem("dev_mode");

    // 清掉 URL 上的参数（保持地址栏整洁）
    if (urlRole || devParam !== null) {
      const cleaned = location.pathname + location.hash;
      history.replaceState({}, "", cleaned);
    }

    // 兼容老的 sessionStorage 设置（迁移一次）
    const legacy = sessionStorage.getItem("mock_role");
    if (legacy && !localStorage.getItem("mock_role")) {
      localStorage.setItem("mock_role", legacy);
    }
  })();

  function _mockUser() {
    const saved = localStorage.getItem("mock_role") || "employee";
    return MOCK_USERS[saved] || MOCK_USERS.employee;
  }

  function _mockHeaders() {
    const u = _mockUser();
    return { "X-User-Id": u.id, "X-User-Role": u.roles.join(",") };
  }

  // ── Clerk mode ─────────────────────────────────────────────────
  async function _clerkHeaders() {
    if (!global.Clerk) throw new Error("Clerk SDK not loaded");
    const token = await global.Clerk.session.getToken();
    return { Authorization: `Bearer ${token}` };
  }

  function _clerkUser() {
    if (!global.Clerk || !global.Clerk.user) return null;
    const u = global.Clerk.user;
    const roles = u.publicMetadata?.roles || [u.publicMetadata?.role || "employee"];
    return { id: u.id, roles, name: u.fullName || u.username || u.id };
  }

  // ── Public API ─────────────────────────────────────────────────
  const auth = {
    mode: MODE,

    /** 当前是否开启了 dev mode（决定是否显示 internal trace 信息）。
     *  按观众分层的核心入口：UI 组件应该用 auth.isDev() 决定要不要
     *  暴露 tool 调用细节、agent_role、phase 标签等"给开发者/面试官看的"信息。
     */
    isDev() {
      return localStorage.getItem("dev_mode") === "1";
    },

    setDev(on) {
      if (on) localStorage.setItem("dev_mode", "1");
      else localStorage.removeItem("dev_mode");
    },

    async getHeaders() {
      if (MODE === "clerk") return _clerkHeaders();
      return _mockHeaders();
    },

    getUser() {
      if (MODE === "clerk") return _clerkUser();
      return _mockUser();
    },

    /** Switch mock role (dev only) */
    setMockRole(role) {
      if (MODE !== "mock") return;
      localStorage.setItem("mock_role", role);
    },

    async signOut() {
      if (MODE === "clerk" && global.Clerk) {
        await global.Clerk.signOut();
        return;
      }
      localStorage.removeItem("mock_role");
      sessionStorage.removeItem("mock_role");
      location.reload();
    },
  };

  global.auth = auth;
})(window);
