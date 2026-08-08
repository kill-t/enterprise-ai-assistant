import type { ApiErrorPayload } from "@/api/contracts";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export interface ApiRequestOptions extends Omit<RequestInit, "headers"> {
  token?: string | null;
  headers?: HeadersInit;
}

/** 保存 HTTP 状态和稳定业务错误码，供页面决定提示或退出登录。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(
    message: string,
    options: { status: number; code: string; details?: unknown },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
  }
}

/** 拼接可选部署前缀，同时保证调用方传入的是绝对 API 路径。 */
function buildApiUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error("API 路径必须以 / 开头");
  }
  return `${API_BASE_URL}${path}`;
}

/** 尽力解析 FastAPI 错误正文，非 JSON 响应使用稳定兜底提示。 */
async function buildApiError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload | null = null;
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // 代理或网关可能返回 HTML，不能让 JSON 解析错误覆盖真实 HTTP 状态。
  }

  return new ApiError(payload?.message ?? "请求失败，请稍后重试", {
    status: response.status,
    code: payload?.code ?? `HTTP_${response.status}`,
    details: payload?.data ?? payload?.detail,
  });
}

/** 识别浏览器主动取消，不把停止生成误包装成普通网络错误。 */
function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/** 统一附加认证头并处理非 2xx，流式和普通请求共同复用。 */
export async function requestApi(
  path: string,
  options: ApiRequestOptions = {},
): Promise<Response> {
  const { token, headers: providedHeaders, ...requestOptions } = options;
  const headers = new Headers(providedHeaders);
  headers.set("Accept", headers.get("Accept") ?? "application/json");

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (
    typeof requestOptions.body === "string" &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl(path), {
      ...requestOptions,
      headers,
    });
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause;
    }
    throw new ApiError("无法连接服务器，请检查网络后重试", {
      status: 0,
      code: "NETWORK_ERROR",
      details: cause,
    });
  }
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return response;
}

/** 请求并解析成功 JSON；空响应按调用方声明的类型返回 undefined。 */
export async function requestJson<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await requestApi(path, options);
  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch (cause) {
    throw new ApiError("服务器返回了无法解析的数据", {
      status: response.status,
      code: "INVALID_JSON_RESPONSE",
      details: cause,
    });
  }
}
