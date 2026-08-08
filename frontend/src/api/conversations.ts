import { requestJson } from "@/api/client";
import type {
  ConversationCreateInput,
  ConversationResponse,
  ConversationUpdateInput,
  MessageResponse,
} from "@/api/contracts";

export interface ListConversationsOptions {
  includeArchived?: boolean;
}

/** 统一编码会话 ID，避免 URL 中的特殊字符改变原本的路径结构。 */
function conversationPath(conversationId: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}`;
}

/** 读取当前用户的会话；默认由后端过滤已归档会话。 */
export async function listConversations(
  token: string,
  options: ListConversationsOptions = {},
): Promise<ConversationResponse[]> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  return requestJson<ConversationResponse[]>(`/api/v1/conversations${query}`, {
    method: "GET",
    token,
  });
}

/** 读取单个会话的元数据；后端会同时验证当前用户是否拥有该会话。 */
export async function getConversation(
  token: string,
  conversationId: string,
): Promise<ConversationResponse> {
  return requestJson<ConversationResponse>(conversationPath(conversationId), {
    method: "GET",
    token,
  });
}

/** 按后端创建时间顺序读取指定会话已经持久化的全部消息。 */
export async function listConversationMessages(
  token: string,
  conversationId: string,
): Promise<MessageResponse[]> {
  return requestJson<MessageResponse[]>(
    `${conversationPath(conversationId)}/messages`,
    {
      method: "GET",
      token,
    },
  );
}

/** 创建一个属于当前 Token 对应用户的新会话。 */
export async function createConversation(
  token: string,
  input: ConversationCreateInput,
): Promise<ConversationResponse> {
  return requestJson<ConversationResponse>("/api/v1/conversations", {
    method: "POST",
    token,
    body: JSON.stringify(input),
  });
}

/** 修改会话标题或归档状态；具体的字段合法性由 FastAPI 再次校验。 */
export async function updateConversation(
  token: string,
  conversationId: string,
  input: ConversationUpdateInput,
): Promise<ConversationResponse> {
  return requestJson<ConversationResponse>(conversationPath(conversationId), {
    method: "PATCH",
    token,
    body: JSON.stringify(input),
  });
}

/** 删除会话及其关联消息；204 空响应会由 requestJson 转换为 undefined。 */
export async function deleteConversation(
  token: string,
  conversationId: string,
): Promise<void> {
  await requestJson<void>(conversationPath(conversationId), {
    method: "DELETE",
    token,
  });
}
