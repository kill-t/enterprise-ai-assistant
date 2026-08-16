import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as conversationsApi from "@/api/conversations";
import * as bindingApi from "@/api/conversation-knowledge-bases";
import * as knowledgeBasesApi from "@/api/knowledge-bases";
import * as streamApi from "@/api/conversation-stream";
import type {
  ConversationResponse,
  MessageResponse,
  MessageSource,
  MessageStreamEvent,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import ConversationDetailView from "@/views/ConversationDetailView.vue";

vi.mock("@/api/conversations", () => ({
  getConversation: vi.fn(),
  listConversationMessages: vi.fn(),
  retryConversationMessage: vi.fn(),
}));

vi.mock("@/api/conversation-stream", () => ({
  streamConversationMessage: vi.fn(),
}));

vi.mock("@/api/conversation-knowledge-bases", () => ({
  getConversationKnowledgeBases: vi.fn(),
  replaceConversationKnowledgeBases: vi.fn(),
}));

vi.mock("@/api/knowledge-bases", () => ({
  archiveKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  updateKnowledgeBase: vi.fn(),
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

/** 用固定事件序列模拟模型逐段生成，不依赖真实 SSE 网络连接。 */
async function* eventStream(
  events: MessageStreamEvent[],
): AsyncGenerator<MessageStreamEvent> {
  for (const event of events) {
    yield event;
  }
}

/** 创建可由测试决定何时返回的重试请求，用于观察按钮禁用状态。 */
function deferred<T>() {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

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
        path: "/knowledge-bases",
        name: "knowledge-bases",
        component: { template: "<p>知识库</p>" },
      },
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
  vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
  vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
    conversation_id: "conversation-1",
    knowledge_bases: [],
  });
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

  it("只把助手回答渲染为 Markdown，用户输入始终保持纯文本", async () => {
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([
      { ...messages[0], content: "**用户输入不应加粗**" },
      {
        ...messages[1],
        content: [
          "## 助学金标准",
          "",
          "| 等级 | 金额 |",
          "| --- | --- |",
          "| 一等 | 3800 元 |",
        ].join("\n"),
      },
    ]);

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    const userMessage = wrapper.get(".message-item--user .message-content");
    expect(userMessage.text()).toBe("**用户输入不应加粗**");
    expect(userMessage.find("strong").exists()).toBe(false);
    const assistantMessage = wrapper.get(
      '.message-item--assistant [data-test="assistant-markdown-content"]',
    );
    expect(assistantMessage.get("h2").text()).toBe("助学金标准");
    expect(assistantMessage.get("table").text()).toContain("3800 元");
  });

  it("在助手历史消息正文下展示持久化引用", async () => {
    const source: MessageSource = {
      knowledge_base_id: "knowledge-base-1",
      knowledge_base_name: "项目资料库",
      document_id: "document-1",
      chunk_id: "chunk-1",
      filename: "项目计划.pdf",
      chunk_index: 0,
      content: "项目第一阶段需要完成需求确认。",
      heading_path: "实施计划 / 第一阶段",
      page_start: 2,
      page_end: 2,
      source_order: 1,
      score: 0.9,
    };
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([
      messages[0],
      { ...messages[1], citations: [source] },
    ]);

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    const citationList = wrapper.get('[data-test="citation-list"]');
    expect(citationList.text()).toContain("参考来源（1）");
    expect(citationList.text()).toContain("项目计划.pdf");
    expect(citationList.text()).toContain("第 2 页");
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

  it("加载会话时同时展示该会话已经持久化的知识库绑定", async () => {
    const boundKnowledgeBase = {
      id: "knowledge-base-1",
      name: "项目资料库",
      description: "项目规范和会议资料",
      embedding_provider: "mock",
      embedding_model: "mock-hash-1024",
      embedding_dimension: 1024,
      is_archived: false,
      created_at: "2026-08-11T01:00:00Z",
      updated_at: "2026-08-11T01:00:00Z",
    };
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([]);
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      boundKnowledgeBase,
    ]);
    vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
      conversation_id: conversation.id,
      knowledge_bases: [boundKnowledgeBase],
    });

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();

    expect(bindingApi.getConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      conversation.id,
    );
    expect(wrapper.get('[data-test="knowledge-context-summary"]').text()).toContain(
      "项目资料库",
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

  it("提交问题后显示 started、delta 和 completed 产生的新消息", async () => {
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue(messages);
    const pendingAssistant: MessageResponse = {
      ...messages[1],
      id: "message-4",
      content: "",
      status: "pending",
      finished_at: null,
    };
    const completedAssistant: MessageResponse = {
      ...pendingAssistant,
      content: "会议简报已开始整理。",
      status: "completed",
      finished_at: "2026-08-08T00:02:00Z",
    };
    vi.mocked(streamApi.streamConversationMessage).mockReturnValue(
      eventStream([
        {
          type: "started",
          user_message: {
            ...messages[0],
            id: "message-3",
            content: "请整理会议简报",
          },
          assistant_message: pendingAssistant,
        },
        {
          type: "delta",
          message_id: pendingAssistant.id,
          delta: "会议简报已开始整理。",
        },
        { type: "completed", message: completedAssistant },
      ]),
    );

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();
    await wrapper.get('[data-test="message-input"]').setValue(" 请整理会议简报 ");
    await wrapper.get('[data-test="message-form"]').trigger("submit");
    await flushPromises();

    expect(streamApi.streamConversationMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "conversation-1",
        content: "请整理会议简报",
        token: "access-token",
      }),
    );
    expect(wrapper.get('[data-test="message-list"]').text()).toContain(
      "会议简报已开始整理。",
    );
    expect(
      (wrapper.get('[data-test="message-input"]').element as HTMLTextAreaElement)
        .value,
    ).toBe("");
  });

  it("失败助手消息原位重试时禁用按钮，并用同一消息的终态替换内容", async () => {
    const failedAssistantMessage: MessageResponse = {
      ...messages[1],
      status: "failed",
      content: "上次回复中断的片段",
      error_code: "MODEL_TIMEOUT",
      error_message: "模型响应超时，请稍后重试",
      finished_at: "2026-08-08T00:01:03Z",
    };
    const completedRetryMessage: MessageResponse = {
      ...failedAssistantMessage,
      status: "completed",
      content: "重试后会议资料已经整理完成。",
      error_code: null,
      error_message: null,
      finished_at: "2026-08-08T00:01:06Z",
    };
    const retryRequest = deferred<MessageResponse>();
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(conversation);
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([
      messages[0],
      failedAssistantMessage,
    ]);
    vi.mocked(conversationsApi.retryConversationMessage).mockReturnValue(
      retryRequest.promise,
    );

    const { wrapper } = await mountConversationDetailView();
    await flushPromises();
    const retryButton = wrapper.get('[data-test="retry-message-message-2"]');
    await retryButton.trigger("click");
    await wrapper.vm.$nextTick();

    expect(retryButton.text()).toBe("正在重试…");
    expect(retryButton.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("生成中");
    expect(wrapper.text()).not.toContain("上次回复中断的片段");

    retryRequest.resolve(completedRetryMessage);
    await flushPromises();

    expect(conversationsApi.retryConversationMessage).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      "message-2",
    );
    expect(wrapper.text()).toContain("重试后会议资料已经整理完成。");
    expect(wrapper.find('[data-test="retry-message-message-2"]').exists()).toBe(
      false,
    );
  });
});
