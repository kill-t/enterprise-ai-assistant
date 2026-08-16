import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as conversationsApi from "@/api/conversations";
import * as streamApi from "@/api/conversation-stream";
import { ApiError } from "@/api/client";
import type {
  ConversationResponse,
  MessageResponse,
  MessageStreamSource,
  MessageStreamEvent,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationDetailStore } from "@/stores/conversation-detail";

vi.mock("@/api/conversations", () => ({
  getConversation: vi.fn(),
  listConversationMessages: vi.fn(),
  retryConversationMessage: vi.fn(),
}));

vi.mock("@/api/conversation-stream", () => ({
  streamConversationMessage: vi.fn(),
}));

const firstConversation: ConversationResponse = {
  id: "conversation-1",
  title: "项目会议准备",
  model_name: "gpt-5",
  temperature: 0.7,
  total_tokens: 120,
  is_archived: false,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

const secondConversation: ConversationResponse = {
  ...firstConversation,
  id: "conversation-2",
  title: "产品评审准备",
};

const firstMessage: MessageResponse = {
  id: "message-1",
  role: "user",
  content: "帮我准备项目会议。",
  model_name: "gpt-5",
  status: "completed",
  error_code: null,
  error_message: null,
  created_at: "2026-08-08T00:01:00Z",
  finished_at: "2026-08-08T00:01:00Z",
};

const pendingAssistantMessage: MessageResponse = {
  ...firstMessage,
  id: "message-2",
  role: "assistant",
  content: "",
  status: "pending",
  finished_at: null,
};

const completedAssistantMessage: MessageResponse = {
  ...pendingAssistantMessage,
  content: "会议资料已整理完成。",
  status: "completed",
  finished_at: "2026-08-08T00:01:02Z",
};

const messageSources: MessageStreamSource[] = [
  {
    knowledge_base_id: "00000000-0000-0000-0000-000000000010",
    knowledge_base_name: "项目资料",
    document_id: "00000000-0000-0000-0000-000000000011",
    chunk_id: "00000000-0000-0000-0000-000000000012",
    filename: "release-guide.pdf",
    chunk_index: 0,
    content: "发布前必须完成安全评审。",
    heading_path: "项目 > 发布",
    page_start: 2,
    page_end: 2,
    source_type: "pdf",
    source_locator: {
      kind: "page",
      page_start: 2,
      page_end: 2,
      heading_path: ["项目", "发布"],
    },
    source_order: 1,
    score: 0.93,
  },
];

/** 用数组模拟一次完整、可预测的内部 SSE 业务事件流。 */
async function* eventStream(
  events: MessageStreamEvent[],
): AsyncGenerator<MessageStreamEvent> {
  for (const event of events) {
    yield event;
  }
}

/** 创建可由测试控制完成时机的 Promise，用于验证并发加载顺序。 */
function deferred<T>() {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

/** 为详情 Store 提供当前用户的最小认证状态。 */
function setAuthenticatedToken(): void {
  useAuthStore().token = "access-token";
}

describe("useConversationDetailStore", () => {
  it("并行读取会话元数据和消息历史，并保存同一次加载结果", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.getConversation).mockResolvedValue(
      firstConversation,
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([
      firstMessage,
    ]);
    const store = useConversationDetailStore();

    await store.load("conversation-1");

    expect(conversationsApi.getConversation).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(conversationsApi.listConversationMessages).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(store.conversation).toEqual(firstConversation);
    expect(store.messages).toEqual([firstMessage]);
    expect(store.hasLoaded).toBe(true);
    expect(store.isLoading).toBe(false);
  });

  it("首次加载失败时清空详情并显示后端错误", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.getConversation).mockRejectedValue(
      new ApiError("会话不存在或无权访问", {
        status: 404,
        code: "CONVERSATION_NOT_FOUND",
      }),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([]);
    const store = useConversationDetailStore();

    await store.load("missing-conversation");

    expect(store.conversation).toBeNull();
    expect(store.messages).toEqual([]);
    expect(store.hasLoaded).toBe(false);
    expect(store.errorMessage).toBe("会话不存在或无权访问");
  });

  it("刷新同一会话失败时保留上一次成功详情", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.getConversation).mockResolvedValueOnce(
      firstConversation,
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValueOnce([
      firstMessage,
    ]);
    const store = useConversationDetailStore();
    await store.load("conversation-1");

    vi.mocked(conversationsApi.getConversation).mockRejectedValueOnce(
      new Error("网络暂时不可用"),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValueOnce([]);
    await store.load("conversation-1");

    expect(store.conversation).toEqual(firstConversation);
    expect(store.messages).toEqual([firstMessage]);
    expect(store.hasLoaded).toBe(true);
    expect(store.errorMessage).toBe("加载会话详情失败，请稍后重试");
  });

  it("较晚返回的旧会话请求不能覆盖当前会话", async () => {
    setAuthenticatedToken();
    const firstDetail = deferred<ConversationResponse>();
    const firstMessages = deferred<MessageResponse[]>();
    vi.mocked(conversationsApi.getConversation).mockImplementation(
      (_token, conversationId) =>
        conversationId === "conversation-1"
          ? firstDetail.promise
          : Promise.resolve(secondConversation),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockImplementation(
      (_token, conversationId) =>
        conversationId === "conversation-1"
          ? firstMessages.promise
          : Promise.resolve([]),
    );
    const store = useConversationDetailStore();

    const firstLoad = store.load("conversation-1");
    const secondLoad = store.load("conversation-2");
    await secondLoad;

    firstDetail.resolve(firstConversation);
    firstMessages.resolve([firstMessage]);
    await firstLoad;

    expect(store.conversationId).toBe("conversation-2");
    expect(store.conversation).toEqual(secondConversation);
    expect(store.messages).toEqual([]);
  });

  it("没有 Token 时不请求详情接口，并清空旧状态", async () => {
    const store = useConversationDetailStore();
    store.conversation = firstConversation;
    store.messages = [firstMessage];

    await store.load("conversation-1");

    expect(conversationsApi.getConversation).not.toHaveBeenCalled();
    expect(conversationsApi.listConversationMessages).not.toHaveBeenCalled();
    expect(store.conversation).toBeNull();
    expect(store.messages).toEqual([]);
    expect(store.errorMessage).toBe("登录状态已失效，请重新登录");
  });

  it("把 started、sources、delta 和 completed 合并为带来源的最终消息", async () => {
    setAuthenticatedToken();
    vi.mocked(streamApi.streamConversationMessage).mockReturnValue(
      eventStream([
        {
          type: "started",
          user_message: { ...firstMessage, content: "整理会议资料" },
          assistant_message: pendingAssistantMessage,
        },
        {
          type: "sources",
          message_id: pendingAssistantMessage.id,
          sources: messageSources,
        },
        {
          type: "delta",
          message_id: pendingAssistantMessage.id,
          delta: "会议资料",
        },
        {
          type: "delta",
          message_id: pendingAssistantMessage.id,
          delta: "已整理完成。",
        },
        { type: "completed", message: completedAssistantMessage },
      ]),
    );
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;

    await store.sendMessage("  整理会议资料  ");

    expect(streamApi.streamConversationMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "conversation-1",
        content: "整理会议资料",
        token: "access-token",
      }),
    );
    expect(store.messages).toEqual([
      { ...firstMessage, content: "整理会议资料" },
      { ...completedAssistantMessage, citations: messageSources },
    ]);
    expect(store.isSending).toBe(false);
    expect(store.sendErrorMessage).toBeNull();
  });

  it("error 终点保留失败助手消息，并显示安全错误", async () => {
    setAuthenticatedToken();
    const failedAssistantMessage: MessageResponse = {
      ...pendingAssistantMessage,
      status: "failed",
      error_code: "MODEL_TIMEOUT",
      error_message: "模型响应超时，请稍后重试",
      finished_at: "2026-08-08T00:01:03Z",
    };
    vi.mocked(streamApi.streamConversationMessage).mockReturnValue(
      eventStream([
        {
          type: "started",
          user_message: firstMessage,
          assistant_message: pendingAssistantMessage,
        },
        { type: "error", message: failedAssistantMessage },
      ]),
    );
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;

    await store.sendMessage("整理会议资料");

    expect(store.messages.at(-1)).toEqual(failedAssistantMessage);
    expect(store.sendErrorMessage).toBe("模型响应超时，请稍后重试");
    expect(store.isSending).toBe(false);
  });

  it("取消中的流会把已经开始的助手消息标为已取消", async () => {
    setAuthenticatedToken();
    vi.mocked(streamApi.streamConversationMessage).mockImplementation((input) =>
      (async function* (): AsyncGenerator<MessageStreamEvent> {
        yield {
          type: "started",
          user_message: firstMessage,
          assistant_message: pendingAssistantMessage,
        };
        await new Promise<never>((_resolve, reject) => {
          input.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("请求已取消", "AbortError")),
            { once: true },
          );
        });
      })(),
    );
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;

    const sending = store.sendMessage("整理会议资料");
    await vi.waitFor(() => {
      expect(store.messages).toHaveLength(2);
    });
    store.cancelActiveStream();
    await sending;

    expect(store.messages.at(-1)).toMatchObject({
      id: pendingAssistantMessage.id,
      status: "cancelled",
    });
    expect(store.isSending).toBe(false);
    expect(store.isCancelling).toBe(false);
  });

  it("重试时原位切换同一条助手消息，并在完成前禁止重复重试", async () => {
    setAuthenticatedToken();
    const failedAssistantMessage: MessageResponse = {
      ...pendingAssistantMessage,
      content: "上次回复中断的片段",
      status: "failed",
      error_code: "MODEL_TIMEOUT",
      error_message: "模型响应超时，请稍后重试",
      finished_at: "2026-08-08T00:01:03Z",
    };
    const completedRetryMessage: MessageResponse = {
      ...failedAssistantMessage,
      content: "重试后会议资料已经整理完成。",
      status: "completed",
      error_code: null,
      error_message: null,
      finished_at: "2026-08-08T00:01:06Z",
    };
    const retryRequest = deferred<MessageResponse>();
    vi.mocked(conversationsApi.retryConversationMessage).mockReturnValue(
      retryRequest.promise,
    );
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;
    store.messages = [firstMessage, failedAssistantMessage];

    const retrying = store.retryMessage(failedAssistantMessage.id);

    expect(store.messages.at(-1)).toMatchObject({
      id: failedAssistantMessage.id,
      status: "pending",
      content: "",
      error_code: null,
      error_message: null,
    });
    expect(store.isRetryingMessage(failedAssistantMessage.id)).toBe(true);
    await expect(store.retryMessage(failedAssistantMessage.id)).resolves.toBeUndefined();

    retryRequest.resolve(completedRetryMessage);
    await retrying;

    expect(conversationsApi.retryConversationMessage).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      failedAssistantMessage.id,
    );
    expect(store.messages.at(-1)).toEqual(completedRetryMessage);
    expect(store.isRetryingMessage(failedAssistantMessage.id)).toBe(false);
  });

  it("重试请求失败时恢复原消息，并保存本次重试错误", async () => {
    setAuthenticatedToken();
    const failedAssistantMessage: MessageResponse = {
      ...pendingAssistantMessage,
      content: "上次回复中断的片段",
      status: "failed",
      error_code: "MODEL_TIMEOUT",
      error_message: "模型响应超时，请稍后重试",
      finished_at: "2026-08-08T00:01:03Z",
    };
    vi.mocked(conversationsApi.retryConversationMessage).mockRejectedValue(
      new ApiError("重试服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;
    store.messages = [firstMessage, failedAssistantMessage];

    await store.retryMessage(failedAssistantMessage.id);

    expect(store.messages.at(-1)).toEqual(failedAssistantMessage);
    expect(store.retryErrorMessages[failedAssistantMessage.id]).toBe(
      "重试服务暂时不可用",
    );
    expect(store.isRetryingMessage(failedAssistantMessage.id)).toBe(false);
  });

  it("重试被模型长度上限截断时重新读取数据库失败终态", async () => {
    setAuthenticatedToken();
    const failedAssistantMessage: MessageResponse = {
      ...pendingAssistantMessage,
      content: "上次回复中断的片段",
      status: "failed",
      error_code: "MODEL_TIMEOUT",
      error_message: "模型响应超时，请稍后重试",
      finished_at: "2026-08-08T00:01:03Z",
    };
    const truncatedMessage: MessageResponse = {
      ...failedAssistantMessage,
      content: "本次生成到输出上限的部分正文",
      model_name: "deepseek-v4-flash",
      error_code: "MODEL_OUTPUT_TRUNCATED",
      error_message: "模型回答达到长度上限，请重试",
      finished_at: "2026-08-08T00:01:08Z",
    };
    vi.mocked(conversationsApi.retryConversationMessage).mockRejectedValue(
      new ApiError("模型回答达到长度上限，请重试", {
        status: 502,
        code: "MODEL_OUTPUT_TRUNCATED",
      }),
    );
    vi.mocked(conversationsApi.listConversationMessages).mockResolvedValue([
      firstMessage,
      truncatedMessage,
    ]);
    const store = useConversationDetailStore();
    store.conversationId = "conversation-1";
    store.hasLoaded = true;
    store.messages = [firstMessage, failedAssistantMessage];

    await store.retryMessage(failedAssistantMessage.id);

    expect(conversationsApi.listConversationMessages).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(store.messages.at(-1)).toEqual(truncatedMessage);
    expect(store.retryErrorMessages[failedAssistantMessage.id]).toBeUndefined();
    expect(store.isRetryingMessage(failedAssistantMessage.id)).toBe(false);
  });
});
