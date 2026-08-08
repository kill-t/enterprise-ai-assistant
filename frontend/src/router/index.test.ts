import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory } from "vue-router";

import * as authApi from "@/api/auth";
import { ApiError } from "@/api/client";
import { createAppRouter } from "@/router";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/api/auth", () => ({
  login: vi.fn(),
  getCurrentUser: vi.fn(),
}));

/** 构造路由恢复成功时返回的当前用户。 */
function currentUser() {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    email: "demo@example.com",
    is_active: true,
    created_at: "2026-08-08T12:00:00Z",
    updated_at: "2026-08-08T12:00:00Z",
  };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("认证路由守卫", () => {
  it("没有 Token 访问受保护页面时跳转登录并保留原地址", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(pinia, createMemoryHistory());

    await router.push("/");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("login");
    expect(router.currentRoute.value.query.redirect).toBe("/");
    expect(authApi.getCurrentUser).not.toHaveBeenCalled();
  });

  it("有效 Token 恢复用户后不能继续停留在登录页", async () => {
    sessionStorage.setItem(
      "enterprise-ai-assistant.access-token",
      "valid-token",
    );
    vi.mocked(authApi.getCurrentUser).mockResolvedValue(currentUser());
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(pinia, createMemoryHistory());

    await router.push("/login");
    await router.isReady();

    const store = useAuthStore();
    expect(router.currentRoute.value.name).toBe("home");
    expect(store.user?.email).toBe("demo@example.com");
    expect(authApi.getCurrentUser).toHaveBeenCalledWith("valid-token");
  });

  it("Token 返回 401 时清理会话并跳转登录", async () => {
    sessionStorage.setItem(
      "enterprise-ai-assistant.access-token",
      "expired-token",
    );
    vi.mocked(authApi.getCurrentUser).mockRejectedValue(
      new ApiError("登录状态已失效", {
        status: 401,
        code: "INVALID_TOKEN",
      }),
    );
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(pinia, createMemoryHistory());

    await router.push("/");
    await router.isReady();

    const store = useAuthStore();
    expect(router.currentRoute.value.name).toBe("login");
    expect(store.token).toBeNull();
    expect(sessionStorage.getItem("enterprise-ai-assistant.access-token")).toBeNull();
  });

  it("临时网络错误保留 Token 并允许进入受保护壳页面", async () => {
    sessionStorage.setItem(
      "enterprise-ai-assistant.access-token",
      "offline-token",
    );
    vi.mocked(authApi.getCurrentUser).mockRejectedValue(
      new ApiError("无法连接服务器，请检查网络后重试", {
        status: 0,
        code: "NETWORK_ERROR",
      }),
    );
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(pinia, createMemoryHistory());

    await router.push("/");
    await router.isReady();

    const store = useAuthStore();
    expect(router.currentRoute.value.name).toBe("home");
    expect(store.token).toBe("offline-token");
    expect(store.sessionError).toBe("无法连接服务器，请检查网络后重试");
  });
});
