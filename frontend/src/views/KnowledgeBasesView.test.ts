import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as knowledgeBasesApi from "@/api/knowledge-bases";
import { ApiError } from "@/api/client";
import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import KnowledgeBasesView from "@/views/KnowledgeBasesView.vue";

vi.mock("@/api/knowledge-bases", () => ({
  archiveKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}));

const knowledgeBase: KnowledgeBaseResponse = {
  id: "knowledge-base-1",
  name: "项目资料库",
  description: "保存项目规范和会议资料",
  embedding_provider: "mock",
  embedding_model: "mock-hash-1024",
  embedding_dimension: 1024,
  is_archived: false,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
};

const archivedKnowledgeBase: KnowledgeBaseResponse = {
  ...knowledgeBase,
  id: "knowledge-base-2",
  name: "旧项目资料库",
  is_archived: true,
};

/** 挂载知识库页，并提供真实 Pinia Store 和最小路由环境。 */
async function mountKnowledgeBasesView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore();
  authStore.token = "access-token";
  authStore.user = {
    id: "user-1",
    email: "demo@example.com",
    is_active: true,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
  };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<p>会话列表</p>" } },
      {
        path: "/knowledge-bases",
        name: "knowledge-bases",
        component: KnowledgeBasesView,
      },
      { path: "/login", name: "login", component: { template: "<p>登录页</p>" } },
    ],
  });
  await router.push("/knowledge-bases");
  await router.isReady();
  const wrapper = mount(KnowledgeBasesView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper, router };
}

/** 创建由测试手动结束的异步请求，用于观察提交中的真实按钮状态。 */
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

describe("KnowledgeBasesView", () => {
  it("挂载后读取并展示当前用户的知识库列表", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);

    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      {},
    );
    const list = wrapper.get('[data-test="knowledge-base-list"]');
    expect(list.text()).toContain("项目资料库");
    expect(list.text()).toContain("保存项目规范和会议资料");
    expect(list.text()).toContain("mock-hash-1024");
    expect(list.text()).toContain("1024 维");
  });

  it("知识库为空时显示业务空状态", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);

    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(wrapper.get('[data-test="knowledge-base-empty-state"]').text()).toContain(
      "还没有知识库",
    );
  });

  it("名称为空或描述过长时显示中文校验错误且不发送请求", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    expect(wrapper.get('[data-test="knowledge-base-form-error"]').text()).toBe(
      "知识库名称不能为空",
    );

    await wrapper.get('[data-test="knowledge-base-name"]').setValue("项目资料库");
    await wrapper
      .get('[data-test="knowledge-base-description"]')
      .setValue("说".repeat(4001));
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");

    expect(wrapper.get('[data-test="knowledge-base-form-error"]').text()).toBe(
      "知识库描述不能超过 4000 个字符",
    );
    expect(knowledgeBasesApi.createKnowledgeBase).not.toHaveBeenCalled();
  });

  it("创建期间禁用表单，成功后清空输入并立即展示新知识库", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
    const createRequest = deferred<KnowledgeBaseResponse>();
    vi.mocked(knowledgeBasesApi.createKnowledgeBase).mockReturnValue(
      createRequest.promise,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="knowledge-base-name"]')
      .setValue("  项目资料库  ");
    await wrapper
      .get('[data-test="knowledge-base-description"]')
      .setValue("  保存项目规范和会议资料  ");
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    await wrapper.vm.$nextTick();

    const submitButton = wrapper.get('[data-test="create-knowledge-base"]');
    expect(submitButton.text()).toBe("正在创建…");
    expect(submitButton.attributes("disabled")).toBeDefined();

    createRequest.resolve(knowledgeBase);
    await flushPromises();

    expect(knowledgeBasesApi.createKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      {
        name: "项目资料库",
        description: "保存项目规范和会议资料",
      },
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
    expect(
      (wrapper.get('[data-test="knowledge-base-name"]').element as HTMLInputElement)
        .value,
    ).toBe("");
    expect(wrapper.get('[data-test="knowledge-base-create-success"]').text()).toContain(
      "知识库创建成功",
    );
  });

  it("创建失败时保留已有知识库并展示后端中文错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.createKnowledgeBase).mockRejectedValue(
      new ApiError("知识库名称已经存在", {
        status: 409,
        code: "KNOWLEDGE_BASE_NAME_EXISTS",
      }),
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="knowledge-base-name"]')
      .setValue("项目资料库");
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("知识库名称已经存在");
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
  });

  it("可以切换到包含归档知识库的列表", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases)
      .mockResolvedValueOnce([knowledgeBase])
      .mockResolvedValueOnce([knowledgeBase, archivedKnowledgeBase]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper.get('[data-test="toggle-archived-knowledge-bases"]').trigger("click");
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenLastCalledWith(
      "access-token",
      { includeArchived: true },
    );
    expect(wrapper.get('[data-test="archived-knowledge-base-badge"]').text()).toBe(
      "已归档",
    );
  });

  it("首次加载失败时可以重新加载", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases)
      .mockRejectedValueOnce(new Error("网络不可用"))
      .mockResolvedValueOnce([knowledgeBase]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("加载知识库失败");
    await wrapper.get('[data-test="retry-load-knowledge-bases"]').trigger("click");
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
  });
});
