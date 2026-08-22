import { afterEach, describe, expect, it, vi } from "vitest";

import { streamConversationMessage } from "@/api/conversation-stream";

/** 创建包含指定 SSE 正文的流式 Response。 */
function createStreamResponse(body: string, status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** 构造后端 MessageResponse 的最小完整测试数据。 */
function message(status: "pending" | "completed") {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    role: "assistant",
    content: status === "completed" ? "会议完成" : "",
    model_name: "mock",
    status,
    error_code: null,
    error_message: null,
    created_at: "2026-08-08T12:00:00Z",
    finished_at: status === "completed" ? "2026-08-08T12:00:01Z" : null,
  };
}

/** 构造一条满足后端范围约束的实时知识来源。 */
function source(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

/** 把消息流完整收集为数组，便于断言事件顺序。 */
async function collectMessageEvents(
  stream: ReturnType<typeof streamConversationMessage>,
) {
  const events = [];
  for await (const event of stream) {
    events.push(event);
  }
  return events;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamConversationMessage", () => {
  it("使用 POST 和认证信息，并保持业务事件顺序", async () => {
    const pending = message("pending");
    const completed = message("completed");
    const body = [
      `event: started\ndata: ${JSON.stringify({
        type: "started",
        user_message: { ...pending, role: "user", status: "completed" },
        assistant_message: pending,
      })}\n\n`,
      `event: sources\ndata: ${JSON.stringify({
        type: "sources",
        message_id: pending.id,
        sources: [source()],
      })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({
        type: "delta",
        message_id: pending.id,
        delta: "会议完成",
      })}\n\n`,
      `event: completed\ndata: ${JSON.stringify({
        type: "completed",
        message: completed,
      })}\n\n`,
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(createStreamResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const events = await collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "整理会议纪要",
        token: "access-token",
      }),
    );

    expect(events.map((event) => event.type)).toEqual([
      "started",
      "sources",
      "delta",
      "completed",
    ]);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "/api/v1/conversations/conversation-1/messages/stream",
    );
    expect(options.method).toBe("POST");
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
    expect(JSON.parse(options.body)).toEqual({ content: "整理会议纪要" });
  });

  it("SSE 建立前的 JSON 错误仍转换为 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "CONVERSATION_NOT_FOUND",
            message: "会话不存在",
            data: null,
          }),
          {
            status: 404,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "missing",
        content: "测试",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toEqual(
      expect.objectContaining({
        status: 404,
        code: "CONVERSATION_NOT_FOUND",
      }),
    );
  });

  it("连接在终点事件前结束时报告业务流不完整", async () => {
    const pending = message("pending");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        createStreamResponse(
          `event: started\ndata: ${JSON.stringify({
            type: "started",
            user_message: { ...pending, role: "user", status: "completed" },
            assistant_message: pending,
          })}\n\n`,
        ),
      ),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "测试不完整流",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toMatchObject({
      code: "MISSING_TERMINAL_EVENT",
    });
  });

  it("event 名称与 JSON type 不一致时拒绝该帧", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        createStreamResponse(
          'event: delta\ndata: {"type":"completed","message":{}}\n\n',
        ),
      ),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "测试错误帧",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toMatchObject({
      code: "EVENT_TYPE_MISMATCH",
    });
  });

  it("来源分数或页码越界时拒绝 sources 帧", async () => {
    const pending = message("pending");
    const body = [
      `event: started\ndata: ${JSON.stringify({
        type: "started",
        user_message: { ...pending, role: "user", status: "completed" },
        assistant_message: pending,
      })}\n\n`,
      `event: sources\ndata: ${JSON.stringify({
        type: "sources",
        message_id: pending.id,
        sources: [source({ score: 1.5, page_start: 0 })],
      })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(createStreamResponse(body)),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "测试非法来源",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toMatchObject({
      code: "INVALID_MESSAGE_EVENT",
    });
  });

  it("未知来源类型或矛盾位置对象不能进入前端状态层", async () => {
    const pending = message("pending");
    const invalidSources = [
      source({ source_type: "pptx" }),
      source({
        source_locator: {
          kind: "page",
          page_start: 3,
          page_end: 2,
          heading_path: [],
        },
      }),
      source({
        source_type: "xlsx",
        source_locator: {
          kind: "sheet_range",
          sheet_name: "Sheet1",
          cell_range: "错误地址",
          header_range: null,
        },
      }),
    ];

    for (const invalidSource of invalidSources) {
      const body = [
        `event: started\ndata: ${JSON.stringify({
          type: "started",
          user_message: { ...pending, role: "user", status: "completed" },
          assistant_message: pending,
        })}\n\n`,
        `event: sources\ndata: ${JSON.stringify({
          type: "sources",
          message_id: pending.id,
          sources: [invalidSource],
        })}\n\n`,
      ].join("");
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(createStreamResponse(body)),
      );

      await expect(
        collectMessageEvents(
          streamConversationMessage({
            conversationId: "conversation-1",
            content: "测试来源位置",
            token: "access-token",
          }),
        ),
      ).rejects.toMatchObject({ code: "INVALID_MESSAGE_EVENT" });
    }
  });

  it("Word 标题前正文可凭内容块位置通过 SSE 校验", async () => {
    const pending = message("pending");
    const wordSource = source({
      filename: "项目方案.docx",
      source_type: "docx",
      heading_path: null,
      source_locator: {
        kind: "section",
        heading_path: [],
        block_kind: "paragraph",
        block_index: 0,
      },
    });
    const body = [
      `event: started\ndata: ${JSON.stringify({
        type: "started",
        user_message: { ...pending, role: "user", status: "completed" },
        assistant_message: pending,
      })}\n\n`,
      `event: sources\ndata: ${JSON.stringify({
        type: "sources",
        message_id: pending.id,
        sources: [wordSource],
      })}\n\n`,
      `event: completed\ndata: ${JSON.stringify({
        type: "completed",
        message: { ...pending, status: "completed", sources: [wordSource] },
      })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(createStreamResponse(body)),
    );

    const events = await collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "读取前言",
        token: "access-token",
      }),
    );

    expect(events[1]).toMatchObject({ type: "sources", sources: [wordSource] });
  });

  it("sources 的消息 ID 与 started 助手不同时拒绝该流", async () => {
    const pending = message("pending");
    const body = [
      `event: started\ndata: ${JSON.stringify({
        type: "started",
        user_message: { ...pending, role: "user", status: "completed" },
        assistant_message: pending,
      })}\n\n`,
      `event: sources\ndata: ${JSON.stringify({
        type: "sources",
        message_id: "00000000-0000-0000-0000-000000000099",
        sources: [source()],
      })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(createStreamResponse(body)),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "测试错误归属",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toMatchObject({
      code: "UNEXPECTED_MESSAGE_ID",
    });
  });

  it("第一个 delta 之后才出现 sources 时拒绝事件乱序", async () => {
    const pending = message("pending");
    const completed = message("completed");
    const body = [
      `event: started\ndata: ${JSON.stringify({
        type: "started",
        user_message: { ...pending, role: "user", status: "completed" },
        assistant_message: pending,
      })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({
        type: "delta",
        message_id: pending.id,
        delta: "先到正文",
      })}\n\n`,
      `event: sources\ndata: ${JSON.stringify({
        type: "sources",
        message_id: pending.id,
        sources: [source()],
      })}\n\n`,
      `event: completed\ndata: ${JSON.stringify({
        type: "completed",
        message: completed,
      })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(createStreamResponse(body)),
    );

    const consume = collectMessageEvents(
      streamConversationMessage({
        conversationId: "conversation-1",
        content: "测试乱序来源",
        token: "access-token",
      }),
    );

    await expect(consume).rejects.toMatchObject({
      code: "INVALID_EVENT_ORDER",
    });
  });
});
