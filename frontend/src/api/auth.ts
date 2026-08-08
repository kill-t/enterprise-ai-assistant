import { requestJson } from "@/api/client";
import type {
  TokenResponse,
  UserRegisterInput,
  UserResponse,
} from "@/api/contracts";

/** 创建用户账号；后端只返回公开用户字段，不在此接口签发 Token。 */
export async function register(
  input: UserRegisterInput,
): Promise<UserResponse> {
  return requestJson<UserResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

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
