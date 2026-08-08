import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as authApi from "@/api/auth";
import { ApiError } from "@/api/client";
import LoginView from "@/views/LoginView.vue";

vi.mock("@/api/auth", () => ({
  login: vi.fn(),
  getCurrentUser: vi.fn(),
}));

/** 为登录组件提供真实 Router，但不引入应用级认证守卫。 */
async function mountLoginView(initialPath = "/login") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/register", name: "register", component: { template: "<p>注册页</p>" } },
      { path: "/", name: "home", component: { template: "<p>首页</p>" } },
      { path: "/workspace", component: { template: "<p>工作区</p>" } },
    ],
  });
  await router.push(initialPath);
  await router.isReady();
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper, router };
}

/** 填写登录表单，字段选择器只服务测试，不绑定视觉样式。 */
async function fillCredentials(
  wrapper: Awaited<ReturnType<typeof mountLoginView>>["wrapper"],
  email = " Demo@Example.com ",
  password = "password123",
) {
  await wrapper.get('[data-test="email"]').setValue(email);
  await wrapper.get('[data-test="password"]').setValue(password);
}

/** 创建可由测试手动完成的 Promise，用于观察提交中的禁用状态。 */
function deferred<T>() {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("LoginView", () => {
  it("保留原受保护地址，提供前往注册页的入口", async () => {
    const { wrapper } = await mountLoginView("/login?redirect=/workspace");

    expect(wrapper.get('[data-test="go-register"]').attributes("href")).toBe(
      "/register?redirect=/workspace",
    );
  });

  it("校验失败时不调用登录接口", async () => {
    const { wrapper } = await mountLoginView();
    await fillCredentials(wrapper, "demo@example.com", "short");

    await wrapper.get("form").trigger("submit");

    expect(authApi.login).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain("密码至少需要 8 位");
  });

  it("提交期间禁用按钮，成功后跳转安全的 redirect 地址", async () => {
    const loginRequest = deferred<Awaited<ReturnType<typeof authApi.login>>>();
    vi.mocked(authApi.login).mockReturnValue(loginRequest.promise);
    const { wrapper, router } = await mountLoginView(
      "/login?redirect=/workspace",
    );
    await fillCredentials(wrapper);

    await wrapper.get("form").trigger("submit");
    await wrapper.vm.$nextTick();

    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('button[type="submit"]').text()).toContain("登录中");
    expect(authApi.login).toHaveBeenCalledWith(
      "demo@example.com",
      "password123",
    );

    loginRequest.resolve({
      access_token: "access-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/workspace");
  });

  it("后端业务错误显示中文提示并恢复按钮", async () => {
    vi.mocked(authApi.login).mockRejectedValue(
      new ApiError("邮箱或密码错误", {
        status: 401,
        code: "INVALID_CREDENTIALS",
      }),
    );
    const { wrapper } = await mountLoginView();
    await fillCredentials(wrapper);

    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toBe("邮箱或密码错误");
    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeUndefined();
  });
});
