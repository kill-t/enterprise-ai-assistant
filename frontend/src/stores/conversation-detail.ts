import { defineStore } from "pinia";

import {
  getConversation,
  listConversationMessages,
} from "@/api/conversations";
import { ApiError } from "@/api/client";
import type {
  ConversationResponse,
  MessageResponse,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";

interface ConversationDetailState {
  conversationId: string | null;
  conversation: ConversationResponse | null;
  messages: MessageResponse[];
  isLoading: boolean;
  hasLoaded: boolean;
  errorMessage: string | null;
  loadRequestVersion: number;
}

/** 将网络和 HTTP 层错误转换为详情页面可直接展示的中文消息。 */
function getErrorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "加载会话详情失败，请稍后重试";
}

/** 管理当前打开会话的元数据和已持久化消息，避免不同会话响应相互覆盖。 */
export const useConversationDetailStore = defineStore("conversation-detail", {
  state: (): ConversationDetailState => ({
    conversationId: null,
    conversation: null,
    messages: [],
    isLoading: false,
    hasLoaded: false,
    errorMessage: null,
    // 仅用于判定异步响应是否仍属于当前请求，不应直接被页面使用。
    loadRequestVersion: 0,
  }),
  actions: {
    /** 清空详情数据并使尚未返回的请求失效，防止退出后写回旧用户信息。 */
    reset(): void {
      this.loadRequestVersion += 1;
      this.conversationId = null;
      this.conversation = null;
      this.messages = [];
      this.isLoading = false;
      this.hasLoaded = false;
      this.errorMessage = null;
    },

    /** 缺少 Token 时不访问受保护接口，同时清理可能残留的详情。 */
    resetForMissingToken(): void {
      this.reset();
      this.errorMessage = "登录状态已失效，请重新登录";
    },

    /** 并行读取会话与历史消息；仅最新一次请求可以修改 Store 状态。 */
    async load(conversationId: string): Promise<void> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return;
      }

      const isSameConversation = this.conversationId === conversationId;
      const requestVersion = this.loadRequestVersion + 1;
      this.loadRequestVersion = requestVersion;
      if (!isSameConversation) {
        // 切换会话时先移除旧消息，避免用户把 A 的内容误认为 B 的内容。
        this.conversationId = conversationId;
        this.conversation = null;
        this.messages = [];
        this.hasLoaded = false;
      }
      this.isLoading = true;
      this.errorMessage = null;

      try {
        const [conversation, messages] = await Promise.all([
          getConversation(token, conversationId),
          listConversationMessages(token, conversationId),
        ]);
        if (this.loadRequestVersion !== requestVersion) {
          return;
        }
        this.conversation = conversation;
        this.messages = messages;
        this.hasLoaded = true;
      } catch (error) {
        if (this.loadRequestVersion !== requestVersion) {
          return;
        }
        this.errorMessage = getErrorMessage(error);
      } finally {
        if (this.loadRequestVersion === requestVersion) {
          this.isLoading = false;
        }
      }
    },
  },
});
