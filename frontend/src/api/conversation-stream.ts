import { requestApi } from "@/api/client";
import type {
  MessageResponse,
  MessageRole,
  MessageStatus,
  MessageStreamEvent,
} from "@/api/contracts";
import {
  parseSseStream,
  SseProtocolError,
  type ParsedSseEvent,
} from "@/api/sse";

const MESSAGE_ROLES = new Set<MessageRole>([
  "system",
  "user",
  "assistant",
  "tool",
]);
const MESSAGE_STATUSES = new Set<MessageStatus>([
  "pending",
  "completed",
  "failed",
  "cancelled",
]);

export interface StreamConversationMessageInput {
  conversationId: string;
  content: string;
  token: string;
  signal?: AbortSignal;
}

/** 判断未知 JSON 是否为普通对象，避免直接访问 null 或数组字段。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 对照后端 MessageResponse 检查运行时字段，防止错误数据进入 Pinia。 */
function isMessageResponse(value: unknown): value is MessageResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.id === "string" &&
    typeof value.role === "string" &&
    MESSAGE_ROLES.has(value.role as MessageRole) &&
    typeof value.content === "string" &&
    typeof value.model_name === "string" &&
    typeof value.status === "string" &&
    MESSAGE_STATUSES.has(value.status as MessageStatus) &&
    (value.error_code === null || typeof value.error_code === "string") &&
    (value.error_message === null || typeof value.error_message === "string") &&
    typeof value.created_at === "string" &&
    (value.finished_at === null || typeof value.finished_at === "string")
  );
}

/** 把未知 JSON 收窄成业务事件，并验证 event 行与 data.type 一致。 */
function decodeMessageEvent(frame: ParsedSseEvent): MessageStreamEvent {
  if (!isRecord(frame.data) || frame.data.type !== frame.event) {
    throw new SseProtocolError(
      "EVENT_TYPE_MISMATCH",
      "SSE event 名称与 JSON type 不一致",
      frame,
    );
  }

  const data = frame.data;
  switch (data.type) {
    case "started":
      if (
        isMessageResponse(data.user_message) &&
        isMessageResponse(data.assistant_message)
      ) {
        return data as unknown as MessageStreamEvent;
      }
      break;
    case "delta":
      if (
        typeof data.message_id === "string" &&
        typeof data.delta === "string" &&
        data.delta.length > 0
      ) {
        return data as unknown as MessageStreamEvent;
      }
      break;
    case "completed":
    case "error":
      if (isMessageResponse(data.message)) {
        return data as unknown as MessageStreamEvent;
      }
      break;
  }

  throw new SseProtocolError(
    "INVALID_MESSAGE_EVENT",
    "SSE 消息事件字段不完整",
    frame,
  );
}

/** 校验 HTTP 响应确实提供可读取的 SSE 字节流。 */
function requireSseBody(response: Response): ReadableStream<Uint8Array> {
  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.toLowerCase().includes("text/event-stream")) {
    throw new SseProtocolError(
      "UNEXPECTED_CONTENT_TYPE",
      "流式接口没有返回 text/event-stream",
      contentType,
    );
  }
  if (!response.body) {
    throw new SseProtocolError(
      "MISSING_RESPONSE_BODY",
      "浏览器没有提供可读取的响应流",
    );
  }
  return response.body;
}

/** 发送消息并按 started、delta、completed/error 顺序产生类型安全事件。 */
export async function* streamConversationMessage(
  input: StreamConversationMessageInput,
): AsyncGenerator<MessageStreamEvent> {
  const response = await requestApi(
    `/api/v1/conversations/${encodeURIComponent(input.conversationId)}/messages/stream`,
    {
      method: "POST",
      token: input.token,
      signal: input.signal,
      body: JSON.stringify({ content: input.content }),
      headers: { Accept: "text/event-stream" },
    },
  );

  let started = false;
  let terminal = false;
  for await (const frame of parseSseStream(requireSseBody(response))) {
    const event = decodeMessageEvent(frame);
    if (!started && event.type !== "started") {
      throw new SseProtocolError(
        "INVALID_EVENT_ORDER",
        "消息流第一项必须是 started",
        event,
      );
    }
    if (started && event.type === "started") {
      throw new SseProtocolError(
        "INVALID_EVENT_ORDER",
        "消息流不能重复 started",
        event,
      );
    }
    if (terminal) {
      throw new SseProtocolError(
        "INVALID_EVENT_ORDER",
        "消息流终点之后不能继续产生事件",
        event,
      );
    }

    started ||= event.type === "started";
    terminal ||= event.type === "completed" || event.type === "error";
    yield event;
  }

  if (!terminal) {
    throw new SseProtocolError(
      "MISSING_TERMINAL_EVENT",
      "消息流在 completed 或 error 之前结束",
    );
  }
}
