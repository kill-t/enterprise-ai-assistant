import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as conversationsApi from "@/api/conversations";
import { ApiError } from "@/api/client";
import type { ConversationResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";

vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(),
  listConversations: vi.fn(),
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
  title: "新建会议讨论",
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

/** 为需要登录态的 Store 测试准备最小 Token，不依赖真实登录接口。 */
function setAuthenticatedToken(): void {
  useAuthStore().token = "access-token";
}

describe("useConversationStore", () => {
  it("加载会话时传递当前 Token，并保存列表和筛选状态", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.listConversations).mockResolvedValue([
      firstConversation,
    ]);
    const store = useConversationStore();

    await store.loadConversations({ includeArchived: true });

    expect(conversationsApi.listConversations).toHaveBeenCalledWith(
      "access-token",
      { includeArchived: true },
    );
    expect(store.conversations).toEqual([firstConversation]);
    expect(store.includeArchived).toBe(true);
    expect(store.hasLoaded).toBe(true);
    expect(store.isLoading).toBe(false);
  });

  it("加载失败时保留上一次成功列表，并显示后端中文错误", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.listConversations).mockRejectedValue(
      new ApiError("会话服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const store = useConversationStore();
    store.conversations = [firstConversation];
    store.hasLoaded = true;

    await store.loadConversations();

    expect(store.conversations).toEqual([firstConversation]);
    expect(store.hasLoaded).toBe(true);
    expect(store.errorMessage).toBe("会话服务暂时不可用");
    expect(store.isLoading).toBe(false);
  });

  it("没有 Token 时不访问后端，并提示重新登录", async () => {
    const store = useConversationStore();

    await store.loadConversations();

    expect(conversationsApi.listConversations).not.toHaveBeenCalled();
    expect(store.errorMessage).toBe("登录状态已失效，请重新登录");
    expect(store.hasLoaded).toBe(false);
  });

  it("创建成功后把新会话插入列表开头，无需重新加载整个列表", async () => {
    setAuthenticatedToken();
    vi.mocked(conversationsApi.createConversation).mockResolvedValue(
      secondConversation,
    );
    const store = useConversationStore();
    store.conversations = [firstConversation];

    const created = await store.createConversation({
      title: "新建会议讨论",
      temperature: 0.7,
    });

    expect(conversationsApi.createConversation).toHaveBeenCalledWith(
      "access-token",
      { title: "新建会议讨论", temperature: 0.7 },
    );
    expect(created).toEqual(secondConversation);
    expect(store.conversations).toEqual([secondConversation, firstConversation]);
    expect(store.isCreating).toBe(false);
    expect(store.errorMessage).toBeNull();
  });

  it("reset 会清空旧用户残留的会话状态", () => {
    const store = useConversationStore();
    store.conversations = [firstConversation];
    store.hasLoaded = true;
    store.includeArchived = true;
    store.errorMessage = "旧错误";

    store.reset();

    expect(store.conversations).toEqual([]);
    expect(store.hasLoaded).toBe(false);
    expect(store.includeArchived).toBe(false);
    expect(store.errorMessage).toBeNull();
  });
});
