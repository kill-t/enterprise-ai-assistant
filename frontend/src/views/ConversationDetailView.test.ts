import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as conversationsApi from "@/api/conversations";
import type { ConversationResponse, MessageResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import ConversationDetailView from "@/views/ConversationDetailView.vue";

vi.mock("@/api/conversations", () => ({
  getConversation: vi.fn(),
  listConversationMessages: vi.fn(),
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

const messages: MessageResponse[] = [
  {
    id: "message-1",
    role: "user",
    content: "帮我准备项目会议。",
    model_name: "gpt-5",
    status: "completed",
    error_code: null,
    error_message: null,
    created_at: "2026-08-08T00:01:00Z",
    finished_at: "2026-08-08T00:01:00Z",
  },
  {
    id: "message-2",
    role: "assistant",
    content: "好的，我会先整理会议背景和待办事项。",
    model_name: "gpt-5",
    status: "completed",
    error_code: null,
    error_message: null,
    created_at: "2026-08-08T00:01:01Z",
    finished_at: "2026-08-08T00:01:02Z",
  },
];

/** 挂载详情页并将当前路径交给真实 Vue Router 解析。 */
async function mountConversationDetailView(initialPath = "/conversations/conversation-1") {
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
      { path: "/", name: "home", component: { template: "<p>会话列表</p>" } },
      { path: "/login", name: "login", component: { template: "<p>登录页</p>" } },
      {
        path: "/conversations/:conversationId",
        name: "conversation-detail",
        component: ConversationDetailView,
      },
    ],
  });
  await router.push(initialPath);
  await router.isReady();
  const wrapper = mount(ConversationDetailView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper, router };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("ConversationDetailView", () => {
  it("根据路由参数读取并渲染会话标题与历史消息", async () => {
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue(messages);

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    expect(conversationsApi.getConversation).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(wrapper.get("h1").text()).toBe("项目会议准备");
    expect(wrapper.get('[data-test="message-list"]').text()).toContain(
      "帮我准备项目会议。",
    );
    expect(wrapper.get('[data-test="message-list"]').text()).toContain(
      "好的，我会先整理会议背景和待办事项。",
    );
  });

  it("历史为空时显示空态，而不是把它当作加载失败", async () => {
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([]);

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    expect(wrapper.get('[data-test="message-empty-state"]').text()).toContain(
      "还没有消息",
    );
  });

  it("会话不存在或无权访问时显示后端错误和返回列表入口", async () => {
    vi.mocked(conversationsApi.getConversation).mockRejectedValue(
      new Error("会话不存在或无权访问"),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([]);

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("加载会话详情失败");
    expect(wrapper.get('[data-test="back-to-list"]').attributes("href")).toBe(
      "/",
    );
  });

  it("路由参数变更时加载新的会话，而不复用旧会话内容", async () => {
    vi.mocked(conversationsApi.getConversation).mockImplementation(
      (_token, conversationId) =>
        Promise.resolve(
          conversationId === "conversation-1"
            ? conversation
            : { ...conversation, id: "conversation-2", title: "产品评审准备" },
        ),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockImplementation(
      (_token, conversationId) =>
        Promise.resolve(
          conversationId === "conversation-1"
            ? messages
            : [{ ...messages[0], id: "message-3", content: "评审目标是什么？" }],
        ),
    );

    const { wrapper, router } = await mountConversationDetailView();
    await flushPromises();
    await router.push("/conversations/conversation-2");
    await flushPromises();

    expect(wrapper.get("h1").text()).toBe("产品评审准备");
    expect(wrapper.text()).toContain("评审目标是什么？");
  });
});
