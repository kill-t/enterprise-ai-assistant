import { requestApi } from "@/api/client";
import type {
  MessageResponse,
  MessageRole,
  MessageStatus,
  MessageStreamEvent,
  MessageStreamSource,
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
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

/** 检查后端资源和消息身份是否为标准 UUID 字符串。 */
function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

/** 页码允许缺失；存在时必须是从 1 开始的整数。 */
function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || (Number.isInteger(value) && Number(value) > 0);
}

/** 严格校验单条实时来源，避免越界分数和错误页码进入状态层。 */
function isMessageStreamSource(
  value: unknown,
  expectedOrder: number,
): value is MessageStreamSource {
  if (!isRecord(value)) {
    return false;
  }
  if (
    !isUuid(value.knowledge_base_id) ||
    !isUuid(value.document_id) ||
    !isUuid(value.chunk_id) ||
    typeof value.knowledge_base_name !== "string" ||
    value.knowledge_base_name.trim().length === 0 ||
    typeof value.filename !== "string" ||
    value.filename.trim().length === 0 ||
    !Number.isInteger(value.chunk_index) ||
    Number(value.chunk_index) < 0 ||
    typeof value.content !== "string" ||
    value.content.trim().length === 0 ||
    (value.heading_path !== null && typeof value.heading_path !== "string") ||
    !isNullablePositiveInteger(value.page_start) ||
    !isNullablePositiveInteger(value.page_end) ||
    value.source_order !== expectedOrder ||
    typeof value.score !== "number" ||
    !Number.isFinite(value.score) ||
    value.score < -1 ||
    value.score > 1
  ) {
    return false;
  }
  return !(
    value.page_start !== null &&
    value.page_end !== null &&
    value.page_end < value.page_start
  );
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
    case "sources":
      if (
        isUuid(data.message_id) &&
        Array.isArray(data.sources) &&
        data.sources.every((source, index) =>
          isMessageStreamSource(source, index + 1),
        )
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

/** 发送消息并按 started、可选 sources、delta、completed/error 产生类型安全事件。 */
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
  let assistantMessageId: string | null = null;
  let sourcesReceived = false;
  let deltaReceived = false;
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

    if (event.type === "started") {
      started = true;
      assistantMessageId = event.assistant_message.id;
    } else {
      const eventMessageId =
        event.type === "sources" || event.type === "delta"
          ? event.message_id
          : event.message.id;
      if (eventMessageId !== assistantMessageId) {
        throw new SseProtocolError(
          "UNEXPECTED_MESSAGE_ID",
          "SSE 事件不属于 started 创建的助手消息",
          event,
        );
      }
      if (event.type === "sources") {
        if (sourcesReceived || deltaReceived) {
          throw new SseProtocolError(
            "INVALID_EVENT_ORDER",
            "sources 必须在第一个 delta 前且最多出现一次",
            event,
          );
        }
        sourcesReceived = true;
      }
      if (event.type === "delta") {
        deltaReceived = true;
      }
    }

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
