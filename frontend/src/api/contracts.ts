export type MessageRole = "system" | "user" | "assistant" | "tool";

export type MessageStatus = "pending" | "completed" | "failed" | "cancelled";

/** 对应 FastAPI MessageResponse，日期在 HTTP 中使用 ISO 字符串。 */
export interface MessageResponse {
  id: string;
  role: MessageRole;
  content: string;
  model_name: string;
  status: MessageStatus;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface MessageStreamStartedEvent {
  type: "started";
  user_message: MessageResponse;
  assistant_message: MessageResponse;
}

export interface MessageStreamDeltaEvent {
  type: "delta";
  message_id: string;
  delta: string;
}

export interface MessageStreamCompletedEvent {
  type: "completed";
  message: MessageResponse;
}

export interface MessageStreamErrorEvent {
  type: "error";
  message: MessageResponse;
}

export type MessageStreamEvent =
  | MessageStreamStartedEvent
  | MessageStreamDeltaEvent
  | MessageStreamCompletedEvent
  | MessageStreamErrorEvent;

/** 登录成功后 FastAPI 返回的短期访问令牌。 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** 当前用户公开信息，不包含密码和密码哈希。 */
export interface UserResponse {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** 对应 FastAPI ConversationResponse，日期在 HTTP 中使用 ISO 字符串。 */
export interface ConversationResponse {
  id: string;
  title: string;
  model_name: string;
  temperature: number;
  total_tokens: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

/** 对应创建会话请求；字段保持与 FastAPI 契约一致，避免隐式转换。 */
export interface ConversationCreateInput {
  title: string;
  system_prompt?: string | null;
  model_name?: string;
  temperature?: number;
}

/** 对应更新会话请求，调用方至少应提供一个可修改字段。 */
export interface ConversationUpdateInput {
  title?: string;
  is_archived?: boolean;
}

/** FastAPI 业务异常处理器返回的统一错误结构。 */
export interface ApiErrorPayload {
  code?: string;
  message?: string;
  data?: unknown;
  detail?: unknown;
}
