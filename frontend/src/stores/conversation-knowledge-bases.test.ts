import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as bindingApi from "@/api/conversation-knowledge-bases";
import { ApiError } from "@/api/client";
import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationKnowledgeBaseStore } from "@/stores/conversation-knowledge-bases";

vi.mock("@/api/conversation-knowledge-bases", () => ({
  getConversationKnowledgeBases: vi.fn(),
  replaceConversationKnowledgeBases: vi.fn(),
}));

const knowledgeBase = (index: number, archived = false): KnowledgeBaseResponse => ({
  id: `knowledge-base-${index}`,
  name: `资料库 ${index}`,
  description: null,
  embedding_provider: "mock",
  embedding_model: "mock-hash-1024",
  embedding_dimension: 1024,
  is_archived: archived,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

function setAuthenticatedToken(): void {
  useAuthStore().token = "access-token";
}

describe("useConversationKnowledgeBaseStore", () => {
  it("加载绑定后同时保存服务端快照和可编辑草稿", async () => {
    setAuthenticatedToken();
    vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [knowledgeBase(1), knowledgeBase(2)],
    });
    const store = useConversationKnowledgeBaseStore();

    await store.load("conversation-1");

    expect(bindingApi.getConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(store.boundKnowledgeBases.map((item) => item.id)).toEqual([
      "knowledge-base-1",
      "knowledge-base-2",
    ]);
    expect(store.selectedKnowledgeBaseIds).toEqual([
      "knowledge-base-1",
      "knowledge-base-2",
    ]);
    expect(store.hasUnsavedChanges).toBe(false);
  });

  it("快速切换会话时忽略较晚返回的旧绑定", async () => {
    setAuthenticatedToken();
    let finishFirst:
      | ((value: {
          conversation_id: string;
          knowledge_bases: KnowledgeBaseResponse[];
        }) => void)
      | undefined;
    vi.mocked(bindingApi.getConversationKnowledgeBases)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({
        conversation_id: "conversation-2",
        knowledge_bases: [knowledgeBase(2)],
      });
    const store = useConversationKnowledgeBaseStore();

    const firstLoad = store.load("conversation-1");
    await store.load("conversation-2");
    finishFirst?.({
      conversation_id: "conversation-1",
      knowledge_bases: [knowledgeBase(1)],
    });
    await firstLoad;

    expect(store.conversationId).toBe("conversation-2");
    expect(store.selectedKnowledgeBaseIds).toEqual(["knowledge-base-2"]);
  });

  it("草稿最多选择五个知识库，第六个不会写入", () => {
    const store = useConversationKnowledgeBaseStore();
    for (let index = 1; index <= 6; index += 1) {
      store.toggleSelection(`knowledge-base-${index}`);
    }

    expect(store.selectedKnowledgeBaseIds).toHaveLength(5);
    expect(store.selectedKnowledgeBaseIds).not.toContain("knowledge-base-6");
    expect(store.selectionErrorMessage).toBe("每个会话最多选择 5 个知识库");
  });

  it("保存成功后以后端响应替换服务端快照和草稿", async () => {
    setAuthenticatedToken();
    vi.mocked(bindingApi.replaceConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [knowledgeBase(2)],
    });
    const store = useConversationKnowledgeBaseStore();
    store.conversationId = "conversation-1";
    store.boundKnowledgeBases = [knowledgeBase(1)];
    store.selectedKnowledgeBaseIds = ["knowledge-base-2"];

    await expect(store.save()).resolves.toBe(true);

    expect(bindingApi.replaceConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      { knowledge_base_ids: ["knowledge-base-2"] },
    );
    expect(store.boundKnowledgeBases).toEqual([knowledgeBase(2)]);
    expect(store.selectedKnowledgeBaseIds).toEqual(["knowledge-base-2"]);
    expect(store.hasUnsavedChanges).toBe(false);
  });

  it("保存失败时保留用户草稿，并展示后端中文错误", async () => {
    setAuthenticatedToken();
    vi.mocked(bindingApi.replaceConversationKnowledgeBases).mockRejectedValue(
      new ApiError("知识库绑定服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const store = useConversationKnowledgeBaseStore();
    store.conversationId = "conversation-1";
    store.boundKnowledgeBases = [knowledgeBase(1)];
    store.selectedKnowledgeBaseIds = ["knowledge-base-2"];

    await expect(store.save()).resolves.toBe(false);

    expect(store.boundKnowledgeBases).toEqual([knowledgeBase(1)]);
    expect(store.selectedKnowledgeBaseIds).toEqual(["knowledge-base-2"]);
    expect(store.saveErrorMessage).toBe("知识库绑定服务暂时不可用");
    expect(store.hasUnsavedChanges).toBe(true);
  });

  it("仍选中已归档绑定时在前端阻止保存并提示先取消", async () => {
    setAuthenticatedToken();
    const store = useConversationKnowledgeBaseStore();
    store.conversationId = "conversation-1";
    store.boundKnowledgeBases = [knowledgeBase(1, true)];
    store.selectedKnowledgeBaseIds = ["knowledge-base-1"];

    await expect(store.save()).resolves.toBe(false);

    expect(bindingApi.replaceConversationKnowledgeBases).not.toHaveBeenCalled();
    expect(store.selectionErrorMessage).toContain("取消已归档知识库");
  });

  it("取消编辑会恢复最近一次服务端选择", () => {
    const store = useConversationKnowledgeBaseStore();
    store.boundKnowledgeBases = [knowledgeBase(1)];
    store.selectedKnowledgeBaseIds = ["knowledge-base-2"];

    store.discardChanges();

    expect(store.selectedKnowledgeBaseIds).toEqual(["knowledge-base-1"]);
    expect(store.hasUnsavedChanges).toBe(false);
  });

  it("没有 Token 时不请求后端并清空上一账号绑定", async () => {
    const store = useConversationKnowledgeBaseStore();
    store.conversationId = "old-conversation";
    store.boundKnowledgeBases = [knowledgeBase(1)];

    await store.load("conversation-1");

    expect(bindingApi.getConversationKnowledgeBases).not.toHaveBeenCalled();
    expect(store.boundKnowledgeBases).toEqual([]);
    expect(store.errorMessage).toBe("登录状态已失效，请重新登录");
  });
});
