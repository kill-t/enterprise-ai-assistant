import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as conversationsApi from "@/api/conversations";
import { ApiError } from "@/api/client";
import type { ConversationResponse, MessageResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationDetailStore } from "@/stores/conversation-detail";

vi.mock("@/api/conversations", () => ({
  getConversation: vi.fn(),
  listConversationMessages: vi.fn(),
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
});
