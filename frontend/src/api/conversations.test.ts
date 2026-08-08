import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  listConversationMessages,
  retryConversationMessage,
  updateConversation,
} from "@/api/conversations";

const conversation = {
  id: "conversation-1",
  title: "项目会议准备",
  model_name: "gpt-5",
  temperature: 0.7,
  total_tokens: 0,
  is_archived: false,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

const message = {
  id: "message-1",
  role: "assistant" as const,
  content: "会议资料已经整理完成。",
  model_name: "gpt-5",
  status: "completed" as const,
  error_code: null,
  error_message: null,
  created_at: "2026-08-08T00:01:00Z",
  finished_at: "2026-08-08T00:01:02Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("会话 API Client", () => {
  it("默认只读取未归档会话，并传递 Bearer Token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([conversation]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listConversations("access-token");

    expect(result).toEqual([conversation]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/conversations",
      expect.objectContaining({ method: "GET" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("读取归档列表时使用后端约定的 include_archived 查询参数", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([conversation]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await listConversations("access-token", { includeArchived: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/conversations?include_archived=true",
      expect.any(Object),
    );
  });

  it("创建会话时发送 JSON 请求体，并返回后端创建结果", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(conversation), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createConversation("access-token", {
      title: "项目会议准备",
      system_prompt: "你是会议助手",
      model_name: "gpt-5",
      temperature: 0.7,
    });

    expect(result).toEqual(conversation);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/v1/conversations");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      title: "项目会议准备",
      system_prompt: "你是会议助手",
      model_name: "gpt-5",
      temperature: 0.7,
    });
  });

  it("读取会话详情和历史消息时复用编码后的会话路径", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(conversation), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([message]), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await getConversation("access-token", "会议 / 1");
    const messages = await listConversationMessages(
      "access-token",
      "会议 / 1",
    );

    expect(detail).toEqual(conversation);
    expect(messages).toEqual([message]);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/conversations/%E4%BC%9A%E8%AE%AE%20%2F%201",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET" });
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/conversations/%E4%BC%9A%E8%AE%AE%20%2F%201/messages",
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "GET" });
    expect(
      new Headers(fetchMock.mock.calls[1][1].headers).get("Authorization"),
    ).toBe("Bearer access-token");
  });

  it("重试消息时使用 POST，并返回同一条助手消息的最终状态", async () => {
    const retriedMessage = {
      ...message,
      status: "completed",
      content: "重试后会议资料已经整理完成。",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(retriedMessage), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await retryConversationMessage(
      "access-token",
      "会议 / 1",
      "消息 / 1",
    );

    expect(result).toEqual(retriedMessage);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/conversations/%E4%BC%9A%E8%AE%AE%20%2F%201/messages/%E6%B6%88%E6%81%AF%20%2F%201/retry",
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("更新和删除会话时正确编码路径，并处理 204 空响应", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...conversation, is_archived: true }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateConversation("access-token", "会议 / 1", {
      is_archived: true,
    });
    await deleteConversation("access-token", "会议 / 1");

    expect(updated.is_archived).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/conversations/%E4%BC%9A%E8%AE%AE%20%2F%201",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PATCH" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      is_archived: true,
    });
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/conversations/%E4%BC%9A%E8%AE%AE%20%2F%201",
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
  });
});
