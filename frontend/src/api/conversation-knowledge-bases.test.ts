import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getConversationKnowledgeBases,
  replaceConversationKnowledgeBases,
} from "@/api/conversation-knowledge-bases";
import type { ConversationKnowledgeBaseSelectionResponse } from "@/api/contracts";

const selection: ConversationKnowledgeBaseSelectionResponse = {
  conversation_id: "conversation-1",
  knowledge_bases: [
    {
      id: "knowledge-base-1",
      name: "项目资料库",
      description: "项目规范和会议资料",
      embedding_provider: "mock",
      embedding_model: "mock-hash-1024",
      embedding_dimension: 1024,
      is_archived: false,
      created_at: "2026-08-11T01:00:00Z",
      updated_at: "2026-08-11T01:00:00Z",
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("会话知识库绑定 API Client", () => {
  it("读取绑定时编码会话 ID 并传递 Token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(selection), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getConversationKnowledgeBases("access-token", "会话 / 1"),
    ).resolves.toEqual(selection);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/conversations/%E4%BC%9A%E8%AF%9D%20%2F%201/knowledge-bases",
      expect.objectContaining({ method: "GET" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("使用 PUT 发送完整 ID 集合，并采用后端响应作为最终绑定", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(selection), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      replaceConversationKnowledgeBases("access-token", "conversation-1", {
        knowledge_base_ids: ["knowledge-base-1"],
      }),
    ).resolves.toEqual(selection);

    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(
      "/api/v1/conversations/conversation-1/knowledge-bases",
    );
    expect(options.method).toBe("PUT");
    expect(JSON.parse(options.body)).toEqual({
      knowledge_base_ids: ["knowledge-base-1"],
    });
  });

  it("空数组保持解除全部绑定的明确语义", async () => {
    const emptySelection = { ...selection, knowledge_bases: [] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(emptySelection), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await replaceConversationKnowledgeBases(
      "access-token",
      "conversation-1",
      { knowledge_base_ids: [] },
    );

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      knowledge_base_ids: [],
    });
  });
});
