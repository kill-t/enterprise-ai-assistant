import type { Pinia } from "pinia";

import { useConversationDetailStore } from "@/stores/conversation-detail";
import { useConversationStore } from "@/stores/conversations";
import { useKnowledgeBaseStore } from "@/stores/knowledge-bases";

/**
 * 集中清空所有用户私有前端状态；认证退出和路由拒绝访问时共同调用，
 * 防止切换账号后短暂展示上一位用户的会话、消息或知识库。
 */
export function resetUserDataStores(pinia?: Pinia): void {
  useConversationDetailStore(pinia).reset();
  useConversationStore(pinia).reset();
  useKnowledgeBaseStore(pinia).reset();
}
