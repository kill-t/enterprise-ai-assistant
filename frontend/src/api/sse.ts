export interface ParsedSseEvent {
  event: string;
  data: unknown;
}

export type SseProtocolErrorCode =
  | "INVALID_SSE_FRAME"
  | "INVALID_SSE_JSON"
  | "INCOMPLETE_SSE_FRAME"
  | "EVENT_TYPE_MISMATCH"
  | "INVALID_MESSAGE_EVENT"
  | "INVALID_EVENT_ORDER"
  | "MISSING_TERMINAL_EVENT"
  | "MISSING_RESPONSE_BODY"
  | "UNEXPECTED_CONTENT_TYPE";

/** 表示 HTTP 成功但 SSE 文本或业务事件契约被破坏。 */
export class SseProtocolError extends Error {
  readonly code: SseProtocolErrorCode;
  readonly details: unknown;

  constructor(
    code: SseProtocolErrorCode,
    message: string,
    details?: unknown,
  ) {
    super(message);
    this.name = "SseProtocolError";
    this.code = code;
    this.details = details;
  }
}

/** 解析一个完整文本帧，忽略注释行并合并多个 data 字段。 */
function parseFrame(rawFrame: string): ParsedSseEvent {
  let eventName: string | null = null;
  const dataLines: string[] = [];

  for (const rawLine of rawFrame.replaceAll("\r\n", "\n").split("\n")) {
    if (!rawLine || rawLine.startsWith(":")) {
      continue;
    }
    const separatorIndex = rawLine.indexOf(":");
    const field = separatorIndex === -1 ? rawLine : rawLine.slice(0, separatorIndex);
    let value = separatorIndex === -1 ? "" : rawLine.slice(separatorIndex + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (!eventName || dataLines.length === 0) {
    throw new SseProtocolError(
      "INVALID_SSE_FRAME",
      "SSE 事件缺少 event 或 data 字段",
      rawFrame,
    );
  }

  try {
    return {
      event: eventName,
      data: JSON.parse(dataLines.join("\n")) as unknown,
    };
  } catch (cause) {
    throw new SseProtocolError(
      "INVALID_SSE_JSON",
      "SSE data 不是合法 JSON",
      cause,
    );
  }
}

/** 找到当前缓冲区中的第一个空行分隔符，兼容 LF 和 CRLF。 */
function findFrameBoundary(buffer: string): RegExpExecArray | null {
  return /\r?\n\r?\n/.exec(buffer);
}

/** 按网络字节流增量解析 SSE，正确处理 UTF-8 字符被拆到两个 chunk 的情况。 */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ParsedSseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let reachedEnd = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        reachedEnd = true;
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let boundary = findFrameBoundary(buffer);
      while (boundary) {
        const rawFrame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (rawFrame.trim()) {
          yield parseFrame(rawFrame);
        }
        boundary = findFrameBoundary(buffer);
      }
    }

    if (buffer.trim()) {
      throw new SseProtocolError(
        "INCOMPLETE_SSE_FRAME",
        "SSE 连接结束时仍存在未闭合事件",
        buffer,
      );
    }
  } finally {
    if (!reachedEnd) {
      try {
        // 协议错误或调用方提前停止时通知 fetch 释放底层响应连接。
        await reader.cancel();
      } catch {
        // 原始解析错误或 AbortError 更有价值，取消清理失败不能覆盖它。
      }
    }
    reader.releaseLock();
  }
}
