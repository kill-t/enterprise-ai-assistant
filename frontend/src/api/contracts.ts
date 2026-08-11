export type MessageRole = "system" | "user" | "assistant" | "tool";

export type MessageStatus = "pending" | "completed" | "failed" | "cancelled";

/** 历史引用与实时 sources 共用的前端展示字段。 */
export interface MessageSource {
  knowledge_base_id: string | null;
  knowledge_base_name: string;
  document_id: string | null;
  chunk_id: string | null;
  filename: string;
  chunk_index: number;
  content: string;
  heading_path: string | null;
  page_start: number | null;
  page_end: number | null;
  source_order: number;
  score: number;
}

/** 历史接口在公共来源字段外补充持久化引用身份和创建时间。 */
export interface MessageCitationResponse extends MessageSource {
  id: string;
  created_at: string;
}

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
  citations?: MessageSource[];
}

/** 历史 GET 接口保证每条消息都带引用数组，普通消息为空数组。 */
export interface MessageHistoryResponse extends MessageResponse {
  citations: MessageCitationResponse[];
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

/** 实时来源的三个活外键在生成时一定存在，历史删除后才可能变为 null。 */
export interface MessageStreamSource extends MessageSource {
  knowledge_base_id: string;
  document_id: string;
  chunk_id: string;
}

export interface MessageStreamSourcesEvent {
  type: "sources";
  message_id: string;
  sources: MessageStreamSource[];
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
  | MessageStreamSourcesEvent
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

/** 对应 FastAPI UserRegisterRequest，密码只用于请求，绝不保存到 Store。 */
export interface UserRegisterInput {
  email: string;
  password: string;
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

/** 对应知识库公开响应；Embedding 字段是创建时冻结的配置快照。 */
export interface KnowledgeBaseResponse {
  id: string;
  name: string;
  description: string | null;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

/** 创建知识库只允许设置业务名称和可选描述，Embedding 配置由后端统一决定。 */
export interface KnowledgeBaseCreateInput {
  name: string;
  description?: string | null;
}

/**
 * 更新时保留 description 的 null 语义：省略表示不修改，null 表示清空已有描述。
 */
export interface KnowledgeBaseUpdateInput {
  name?: string;
  description?: string | null;
  is_archived?: boolean;
}

/** FastAPI 业务异常处理器返回的统一错误结构。 */
export interface ApiErrorPayload {
  code?: string;
  message?: string;
  data?: unknown;
  detail?: unknown;
}
