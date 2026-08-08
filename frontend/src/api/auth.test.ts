import { afterEach, describe, expect, it, vi } from "vitest";

import { register } from "@/api/auth";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("认证 API Client", () => {
  it("注册时使用 JSON 请求体并返回后端用户公开信息", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "user-1",
          email: "demo@example.com",
          is_active: true,
          created_at: "2026-08-08T00:00:00Z",
          updated_at: "2026-08-08T00:00:00Z",
        }),
        {
          status: 201,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = await register({
      email: "demo@example.com",
      password: "password123",
    });

    expect(user.email).toBe("demo@example.com");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/register",
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      email: "demo@example.com",
      password: "password123",
    });
    expect(new Headers(options.headers).get("Content-Type")).toBe(
      "application/json",
    );
  });
});
