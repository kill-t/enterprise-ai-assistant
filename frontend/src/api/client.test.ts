import { afterEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "@/api/client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestJson", () => {
  it("发送 Bearer Token 并返回成功 JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "user-1", email: "demo@example.com" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await requestJson<{ id: string }>("/api/v1/auth/me", {
      token: "access-token",
    });

    expect(result.id).toBe("user-1");
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("把 FastAPI 业务错误转换为包含状态码和错误码的 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "INVALID_CREDENTIALS",
            message: "邮箱或密码错误",
            data: null,
          }),
          {
            status: 401,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const request = requestJson("/api/v1/auth/me");

    await expect(request).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        status: 401,
        code: "INVALID_CREDENTIALS",
        message: "邮箱或密码错误",
      }),
    );
  });

  it("普通网络故障转换为稳定的 NETWORK_ERROR", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("连接失败")));

    await expect(requestJson("/api/v1/auth/me")).rejects.toEqual(
      expect.objectContaining({
        status: 0,
        code: "NETWORK_ERROR",
        message: "无法连接服务器，请检查网络后重试",
      }),
    );
  });

  it("AbortController 取消信号保持原样传播", async () => {
    const abortError = new DOMException("请求已取消", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    await expect(requestJson("/api/v1/auth/me")).rejects.toBe(abortError);
  });
});
