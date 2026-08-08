import { describe, expect, it } from "vitest";

import { parseSseStream, SseProtocolError } from "@/api/sse";

/** 把字符串片段转换成浏览器 fetch 响应使用的字节流。 */
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

/** 消费异步生成器，方便测试完整事件顺序。 */
async function collectEvents(stream: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of parseSseStream(stream)) {
    events.push(event);
  }
  return events;
}

describe("parseSseStream", () => {
  it("能够跨网络分块解析多个中文 SSE 事件", async () => {
    const stream = streamFromChunks([
      'event: started\r\ndata: {"type":"started"}\r\n',
      '\r\nevent: delta\ndata: {"type":"delta","delta":"会',
      '议"}\n\nevent: completed\ndata: {"type":"completed"}\n\n',
    ]);

    await expect(collectEvents(stream)).resolves.toEqual([
      { event: "started", data: { type: "started" } },
      { event: "delta", data: { type: "delta", delta: "会议" } },
      { event: "completed", data: { type: "completed" } },
    ]);
  });

  it("JSON 损坏时抛出可识别的协议错误", async () => {
    const stream = streamFromChunks([
      "event: delta\ndata: {不是合法JSON}\n\n",
    ]);

    await expect(collectEvents(stream)).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("连接结束时存在未闭合帧应报告流不完整", async () => {
    const stream = streamFromChunks([
      'event: delta\ndata: {"type":"delta","delta":"半截"}',
    ]);

    await expect(collectEvents(stream)).rejects.toMatchObject({
      code: "INCOMPLETE_SSE_FRAME",
    });
  });

  it("协议解析失败时主动取消底层字节流", async () => {
    const encoder = new TextEncoder();
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode("event: delta\ndata: {损坏JSON}\n\n"),
        );
      },
      cancel() {
        cancelled = true;
      },
    });

    await expect(collectEvents(stream)).rejects.toBeInstanceOf(SseProtocolError);
    expect(cancelled).toBe(true);
  });
});
