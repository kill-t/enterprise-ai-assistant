import { requestJson } from "@/api/client";
import type {
  ConversationKnowledgeBaseSelectionResponse,
  ConversationKnowledgeBaseUpdateInput,
} from "@/api/contracts";

/** 统一编码会话 ID，并生成绑定集合的唯一资源路径。 */
function conversationKnowledgeBasesPath(conversationId: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}/knowledge-bases`;
}

/** 读取会话当前绑定；响应可能包含后来被归档的既有知识库。 */
export async function getConversationKnowledgeBases(
  token: string,
  conversationId: string,
): Promise<ConversationKnowledgeBaseSelectionResponse> {
  return requestJson<ConversationKnowledgeBaseSelectionResponse>(
    conversationKnowledgeBasesPath(conversationId),
    {
      method: "GET",
      token,
    },
  );
}

/**
 * 使用 PUT 原子替换完整绑定集合。它不是追加单条关系，空数组也有明确的全部解绑语义。
 */
export async function replaceConversationKnowledgeBases(
  token: string,
  conversationId: string,
  input: ConversationKnowledgeBaseUpdateInput,
): Promise<ConversationKnowledgeBaseSelectionResponse> {
  return requestJson<ConversationKnowledgeBaseSelectionResponse>(
    conversationKnowledgeBasesPath(conversationId),
    {
      method: "PUT",
      token,
      body: JSON.stringify(input),
    },
  );
}
