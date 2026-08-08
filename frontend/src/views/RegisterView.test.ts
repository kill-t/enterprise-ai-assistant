import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as authApi from "@/api/auth";
import { ApiError } from "@/api/client";
import RegisterView from "@/views/RegisterView.vue";

vi.mock("@/api/auth", () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
}));

/** 为注册页面提供真实 Pinia 与最小 Router，不引入应用级路由守卫。 */
async function mountRegisterView(initialPath = "/register") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: { template: "<p>登录页</p>" } },
      { path: "/register", name: "register", component: RegisterView },
      { path: "/", name: "home", component: { template: "<p>首页</p>" } },
      { path: "/workspace", component: { template: "<p>工作区</p>" } },
    ],
  });
  await router.push(initialPath);
  await router.isReady();
  const wrapper = mount(RegisterView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper, router };
}

/** 填写通过客户端校验的注册信息。 */
async function fillRegistrationForm(
  wrapper: Awaited<ReturnType<typeof mountRegisterView>>["wrapper"],
  password = "password123",
  confirmPassword = password,
): Promise<void> {
  await wrapper.get('[data-test="register-email"]').setValue(" Demo@Example.com ");
  await wrapper.get('[data-test="register-password"]').setValue(password);
  await wrapper
    .get('[data-test="register-confirm-password"]')
    .setValue(confirmPassword);
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("RegisterView", () => {
  it("确认密码不一致时不发送注册请求", async () => {
    const { wrapper } = await mountRegisterView();
    await fillRegistrationForm(wrapper, "password123", "different123");

    await wrapper.get("form").trigger("submit");

    expect(authApi.register).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain("两次输入的密码不一致");
  });

  it("注册后自动登录，并跳转到安全 redirect 地址", async () => {
    vi.mocked(authApi.register).mockResolvedValue({
      id: "user-1",
      email: "demo@example.com",
      is_active: true,
      created_at: "2026-08-08T00:00:00Z",
      updated_at: "2026-08-08T00:00:00Z",
    });
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "access-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    const { wrapper, router } = await mountRegisterView(
      "/register?redirect=/workspace",
    );
    await fillRegistrationForm(wrapper);

    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(authApi.register).toHaveBeenCalledWith({
      email: "demo@example.com",
      password: "password123",
    });
    expect(authApi.login).toHaveBeenCalledWith("demo@example.com", "password123");
    expect(router.currentRoute.value.path).toBe("/workspace");
  });

  it("后端注册错误显示中文消息，且按钮恢复可点击状态", async () => {
    vi.mocked(authApi.register).mockRejectedValue(
      new ApiError("该邮箱已注册", {
        status: 409,
        code: "EMAIL_ALREADY_REGISTERED",
      }),
    );
    const { wrapper } = await mountRegisterView();
    await fillRegistrationForm(wrapper);

    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toBe("该邮箱已注册");
    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeUndefined();
  });
});
