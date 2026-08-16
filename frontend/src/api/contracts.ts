export type MessageRole = "system" | "user" | "assistant" | "tool";

export type MessageStatus = "pending" | "completed" | "failed" | "cancelled";

export type SourceType =
  | "pdf"
  | "text"
  | "markdown"
  | "docx"
  | "xlsx"
  | "image";

export interface DocumentSourceLocator {
  kind: "document";
}

export interface PageSourceLocator {
  kind: "page";
  page_start: number;
  page_end: number;
  heading_path: string[];
}

export interface SectionSourceLocator {
  kind: "section";
  heading_path: string[];
  block_kind: "paragraph" | "list" | "table" | null;
  block_index: number | null;
}

export interface SheetRangeSourceLocator {
  kind: "sheet_range";
  sheet_name: string;
  cell_range: string;
  header_range: string | null;
}

export interface NormalizedBoundingBox {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

export interface OcrRegionSourceLocator {
  kind: "ocr_region";
  page_number: number | null;
  bounding_box: NormalizedBoundingBox;
  confidence: number;
  engine: string;
}

/** 使用 kind 判别不同格式的位置，避免把 Excel 单元格硬塞进页码字段。 */
export type SourceLocator =
  | DocumentSourceLocator
  | PageSourceLocator
  | SectionSourceLocator
  | SheetRangeSourceLocator
  | OcrRegionSourceLocator;

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
  source_type: SourceType;
  source_locator: SourceLocator;
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

/** 会话绑定接口返回完整知识库快照，包含后来被归档的既有绑定。 */
export interface ConversationKnowledgeBaseSelectionResponse {
  conversation_id: string;
  knowledge_bases: KnowledgeBaseResponse[];
}

/** PUT 使用完整 ID 集合原子替换旧绑定；空数组表示解除全部绑定。 */
export interface ConversationKnowledgeBaseUpdateInput {
  knowledge_base_ids: string[];
}

/** 文档从等待后台入库到可检索、失败或被删除的业务状态。 */
export type DocumentStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "deleted";

/** 一次持久化入库任务的执行状态；重试会创建新的任务，而不是覆盖旧任务。 */
export type DocumentIngestionJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed";

/** 对应 FastAPI DocumentResponse，包含页面展示和故障定位需要的处理统计。 */
export interface DocumentResponse {
  id: string;
  knowledge_base_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: DocumentStatus;
  parser_version: string | null;
  chunker_version: string | null;
  page_count: number;
  chunk_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

/** 上传接口只确认文件已接收并创建任务，不代表文档已经可以检索。 */
export interface DocumentUploadResponse {
  document: DocumentResponse;
  job_id: string;
  job_status: DocumentIngestionJobStatus;
}

/** 失败重试会返回恢复为 pending 的文档，以及本次新任务的身份。 */
export interface DocumentRetryResponse {
  document: DocumentResponse;
  job_id: string;
  job_status: DocumentIngestionJobStatus;
}

/** FastAPI 业务异常处理器返回的统一错误结构。 */
export interface ApiErrorPayload {
  code?: string;
  message?: string;
  data?: unknown;
  detail?: unknown;
}
