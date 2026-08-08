import { defineStore } from "pinia";

import {
  createConversation as createConversationRequest,
  listConversations,
  type ListConversationsOptions,
} from "@/api/conversations";
import { ApiError } from "@/api/client";
import type {
  ConversationCreateInput,
  ConversationResponse,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";

interface ConversationState {
  conversations: ConversationResponse[];
  isLoading: boolean;
  isCreating: boolean;
  hasLoaded: boolean;
  includeArchived: boolean;
  errorMessage: string | null;
}

/** 将基础设施错误转换为页面可直接展示的中文消息。 */
function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 管理当前用户的会话列表状态；HTTP 协议细节委托给 API Client。 */
export const useConversationStore = defineStore("conversations", {
  state: (): ConversationState => ({
    conversations: [],
    isLoading: false,
    isCreating: false,
    hasLoaded: false,
    includeArchived: false,
    errorMessage: null,
  }),
  actions: {
    /** 缺少 Token 时不发送请求，防止旧页面继续访问受保护接口。 */
    resetForMissingToken(): void {
      this.$reset();
      this.errorMessage = "登录状态已失效，请重新登录";
    },

    /** 加载当前用户会话；失败时保留上一次成功列表，避免界面瞬间清空。 */
    async loadConversations(
      options: ListConversationsOptions = {},
    ): Promise<void> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return;
      }

      this.isLoading = true;
      this.errorMessage = null;
      this.includeArchived = Boolean(options.includeArchived);
      try {
        this.conversations = await listConversations(token, options);
        this.hasLoaded = true;
      } catch (error) {
        this.errorMessage = getErrorMessage(error, "加载会话失败，请稍后重试");
      } finally {
        this.isLoading = false;
      }
    },

    /** 创建成功后直接插入列表首位，避免再发一次完整列表请求。 */
    async createConversation(
      input: ConversationCreateInput,
    ): Promise<ConversationResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }

      this.isCreating = true;
      this.errorMessage = null;
      try {
        const conversation = await createConversationRequest(token, input);
        this.conversations.unshift(conversation);
        return conversation;
      } catch (error) {
        this.errorMessage = getErrorMessage(error, "创建会话失败，请稍后重试");
        return null;
      } finally {
        this.isCreating = false;
      }
    },

    /** 用户退出登录或切换账号时清空当前标签页内的会话数据。 */
    reset(): void {
      this.$reset();
    },
  },
});
