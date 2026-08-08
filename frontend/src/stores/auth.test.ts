import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/auth";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/api/auth", () => ({
  login: vi.fn(),
  getCurrentUser: vi.fn(),
  register: vi.fn(),
}));

beforeEach(() => {
  sessionStorage.clear();
  setActivePinia(createPinia());
});

describe("useAuthStore", () => {
  it("登录成功后同时更新 Pinia 和 sessionStorage", async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "access-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    const store = useAuthStore();

    await store.login("demo@example.com", "password123");

    expect(store.token).toBe("access-token");
    expect(store.isAuthenticated).toBe(true);
    expect(sessionStorage.getItem("enterprise-ai-assistant.access-token")).toBe(
      "access-token",
    );
  });

  it("退出时清理用户状态和浏览器会话令牌", async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "access-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    const store = useAuthStore();
    await store.login("demo@example.com", "password123");

    store.logout();

    expect(store.token).toBeNull();
    expect(store.user).toBeNull();
    expect(sessionStorage.getItem("enterprise-ai-assistant.access-token")).toBeNull();
  });

  it("注册成功后自动登录，并保存当前标签页 Token", async () => {
    vi.mocked(authApi.register).mockResolvedValue({
      id: "user-1",
      email: "demo@example.com",
      is_active: true,
      created_at: "2026-08-08T00:00:00Z",
      updated_at: "2026-08-08T00:00:00Z",
    });
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "registered-access-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    const store = useAuthStore();

    await store.registerAndLogin("demo@example.com", "password123");

    expect(authApi.register).toHaveBeenCalledWith({
      email: "demo@example.com",
      password: "password123",
    });
    expect(authApi.login).toHaveBeenCalledWith("demo@example.com", "password123");
    expect(store.token).toBe("registered-access-token");
    expect(sessionStorage.getItem("enterprise-ai-assistant.access-token")).toBe(
      "registered-access-token",
    );
  });
});
