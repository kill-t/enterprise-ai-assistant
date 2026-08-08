import { requestJson } from "@/api/client";
import type { TokenResponse, UserResponse } from "@/api/contracts";

/** 按 OAuth2 Password Form 约定发送邮箱和密码。 */
export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  return requestJson<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: form,
  });
}

/** 使用当前 Bearer Token 恢复用户公开信息。 */
export async function getCurrentUser(token: string): Promise<UserResponse> {
  return requestJson<UserResponse>("/api/v1/auth/me", { token });
}
