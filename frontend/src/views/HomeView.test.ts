import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as conversationsApi from "@/api/conversations";
import type { ConversationResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import HomeView from "@/views/HomeView.vue";

vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
  updateConversation: vi.fn(),
}));

const conversation: ConversationResponse = {
  id: "conversation-1",
  title: "项目会议准备",
  model_name: "gpt-5",
  temperature: 0.7,
  total_tokens: 120,
  is_archived: false,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

const archivedConversation: ConversationResponse = {
  ...conversation,
  id: "conversation-2",
  title: "已归档会议记录",
  is_archived: true,
};

/** 挂载工作台，并提供真实 Pinia Store 与最小 Router 环境。 */
async function mountHomeView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore();
  authStore.token = "access-token";
  authStore.user = {
    id: "user-1",
    email: "demo@example.com",
    is_active: true,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z",
  };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: HomeView },
      { path: "/login", name: "login", component: { template: "<p>登录页</p>" } },
      {
        path: "/knowledge-bases",
        name: "knowledge-bases",
        component: { template: "<p>知识库</p>" },
      },
      {
        path: "/conversations/:conversationId",
        name: "conversation-detail",
        component: { template: "<p>会话详情</p>" },
      },
    ],
  });
  await router.push("/");
  await router.isReady();
  const wrapper = mount(HomeView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper };
}

/** 创建可由测试手动完成的 Promise，用于观察创建中的按钮状态。 */
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

describe("HomeView", () => {
  it("挂载后自动加载并渲染当前用户的会话列表", async () => {
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([
      conversation,
    ]);

    const { wrapper } = await mountHomeView();
    await flushPromises();

    expect(conversationsApi.listConversations).toHaveBeenCalledWith(
      "access-token",
      {},
    );
    expect(wrapper.get('[data-test="conversation-list"]').text()).toContain(
      "项目会议准备",
    );
    expect(
      wrapper.get('[data-test="conversation-list"] a').attributes("href"),
    ).toBe("/conversations/conversation-1");
    expect(wrapper.text()).toContain("demo@example.com");
    expect(wrapper.get('[data-test="knowledge-bases-link"]').attributes("href")).toBe(
      "/knowledge-bases",
    );
  });

  it("没有会话时显示空态和可用的新建按钮", async () => {
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([]);

    const { wrapper } = await mountHomeView();
    await flushPromises();

    expect(wrapper.get('[data-test="empty-state"]').text()).toContain(
      "还没有会话",
    );
    expect(wrapper.get('[data-test="create-conversation"]').text()).toBe(
      "新建会话",
    );
  });

  it("创建期间禁用按钮，成功后立即渲染新会话且不重新读取列表", async () => {
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([]);
    const createRequest = deferred<ConversationResponse>();
    vi.mocked(conversationsApi.createConversation).mockReturnValue(
      createRequest.promise,
    );

    const { wrapper } = await mountHomeView();
    await flushPromises();
    await wrapper.get('[data-test="create-conversation"]').trigger("click");
    await wrapper.vm.$nextTick();

    expect(wrapper.get('[data-test="create-conversation"]').text()).toBe(
      "正在创建…",
    );
    expect(
      wrapper.get('[data-test="create-conversation"]').attributes("disabled"),
    ).toBeDefined();

    createRequest.resolve(conversation);
    await flushPromises();

    expect(conversationsApi.createConversation).toHaveBeenCalledWith(
      "access-token",
      { title: "新建会话" },
    );
    expect(wrapper.get('[data-test="conversation-list"]').text()).toContain(
      "项目会议准备",
    );
    expect(conversationsApi.listConversations).toHaveBeenCalledTimes(1);
  });

  it("首次加载失败时显示错误，并可点击重试", async () => {
    vi.mocked(conversationsApi.listConversations)
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce([conversation]);

    const { wrapper } = await mountHomeView();
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("加载会话失败");
    await wrapper.get('[data-test="retry-load"]').trigger("click");
    await flushPromises();

    expect(conversationsApi.listConversations).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-test="conversation-list"]').text()).toContain(
      "项目会议准备",
    );
  });

  it("可在重命名对话框中提交新标题，并立即看到后端确认后的结果", async () => {
    const renamedConversation: ConversationResponse = {
      ...conversation,
      title: "项目会议简报",
    };
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([
      conversation,
    ]);
    vi.mocked(conversationsApi.updateConversation).mockResolvedValue(
      renamedConversation,
    );

    const { wrapper } = await mountHomeView();
    await flushPromises();
    await wrapper
      .get('[data-test="rename-conversation-conversation-1"]')
      .trigger("click");

    expect(wrapper.get('[data-test="rename-dialog"]').text()).toContain(
      "重命名会话",
    );
    await wrapper.get('[data-test="rename-input"]').setValue(" 项目会议简报 ");
    await wrapper.get('[data-test="rename-dialog"] form').trigger("submit");
    await flushPromises();

    expect(conversationsApi.updateConversation).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      { title: "项目会议简报" },
    );
    expect(wrapper.text()).toContain("项目会议简报");
    expect(wrapper.find('[data-test="rename-dialog"]').exists()).toBe(false);
  });

  it("归档会话后从默认列表移除该会话", async () => {
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([
      conversation,
    ]);
    vi.mocked(conversationsApi.updateConversation).mockResolvedValue({
      ...conversation,
      is_archived: true,
    });

    const { wrapper } = await mountHomeView();
    await flushPromises();
    await wrapper
      .get('[data-test="archive-conversation-conversation-1"]')
      .trigger("click");
    await flushPromises();

    expect(conversationsApi.updateConversation).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      { is_archived: true },
    );
    expect(wrapper.get('[data-test="empty-state"]').text()).toContain(
      "还没有会话",
    );
  });

  it("删除会先显示确认框，确认后才调用删除接口", async () => {
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([
      conversation,
    ]);
    vi.mocked(conversationsApi.deleteConversation).mockResolvedValue();

    const { wrapper } = await mountHomeView();
    await flushPromises();
    await wrapper
      .get('[data-test="delete-conversation-conversation-1"]')
      .trigger("click");

    expect(wrapper.get('[data-test="delete-dialog"]').text()).toContain(
      "删除会话",
    );
    expect(conversationsApi.deleteConversation).not.toHaveBeenCalled();

    await wrapper.get('[data-test="confirm-delete"]').trigger("click");
    await flushPromises();

    expect(conversationsApi.deleteConversation).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(wrapper.find('[data-test="empty-state"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="delete-dialog"]').exists()).toBe(false);
  });

  it("可切换到包含归档会话的列表，并展示归档标记", async () => {
    vi.mocked(conversationsApi.listConversations)
      .mockResolvedValueOnce([conversation])
      .mockResolvedValueOnce([conversation, archivedConversation]);

    const { wrapper } = await mountHomeView();
    await flushPromises();
    await wrapper.get('[data-test="toggle-archived"]').trigger("click");
    await flushPromises();

    expect(conversationsApi.listConversations).toHaveBeenLastCalledWith(
      "access-token",
      { includeArchived: true },
    );
    expect(wrapper.get('[data-test="archived-badge"]').text()).toBe("已归档");
  });
});
