import { defineStore } from "pinia";

import {
  createConversation as createConversationRequest,
  deleteConversation as deleteConversationRequest,
  listConversations,
  type ListConversationsOptions,
  updateConversation as updateConversationRequest,
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
  updatingConversationIds: string[];
  deletingConversationIds: string[];
  actionErrorMessages: Record<string, string>;
}

/** 将基础设施错误转换为页面可直接展示的中文消息。 */
function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 从会话操作错误集合中移除指定会话，避免旧错误在下次操作时继续显示。 */
function clearActionError(
  errorMessages: Record<string, string>,
  conversationId: string,
): Record<string, string> {
  const { [conversationId]: _removedError, ...remainingErrors } = errorMessages;
  return remainingErrors;
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
    updatingConversationIds: [],
    deletingConversationIds: [],
    actionErrorMessages: {},
  }),
  getters: {
    /** 供单个会话操作按钮判断是否正在发送更新请求。 */
    isUpdatingConversation: (state) => (conversationId: string): boolean =>
      state.updatingConversationIds.includes(conversationId),

    /** 供单个会话删除按钮判断是否正在等待后端确认。 */
    isDeletingConversation: (state) => (conversationId: string): boolean =>
      state.deletingConversationIds.includes(conversationId),

    /** 同一会话存在任意管理请求时都应禁用其管理按钮，防止重复提交。 */
    isManagingConversation: (state) => (conversationId: string): boolean =>
      state.updatingConversationIds.includes(conversationId) ||
      state.deletingConversationIds.includes(conversationId),
  },
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

    /**
     * 重命名只在后端确认后替换列表中的那一项，失败时保留原来的标题，
     * 这样用户不会误以为本地已经保存成功。
     */
    async renameConversation(
      conversationId: string,
      title: string,
    ): Promise<ConversationResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isManagingConversation(conversationId)) {
        return null;
      }

      const normalizedTitle = title.trim();
      if (!normalizedTitle) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [conversationId]: "会话标题不能为空",
        };
        return null;
      }

      this.updatingConversationIds.push(conversationId);
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        conversationId,
      );
      try {
        const updatedConversation = await updateConversationRequest(
          token,
          conversationId,
          { title: normalizedTitle },
        );
        this.conversations = this.conversations.map((conversation) =>
          conversation.id === conversationId ? updatedConversation : conversation,
        );
        return updatedConversation;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [conversationId]: getErrorMessage(error, "更新会话失败，请稍后重试"),
        };
        return null;
      } finally {
        this.updatingConversationIds = this.updatingConversationIds.filter(
          (id) => id !== conversationId,
        );
      }
    },

    /**
     * 已归档会话不属于默认列表，因此默认筛选下直接移除；
     * 若页面正在查看全部会话，则保留该项并更新它的归档状态。
     */
    async archiveConversation(
      conversationId: string,
    ): Promise<ConversationResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isManagingConversation(conversationId)) {
        return null;
      }

      this.updatingConversationIds.push(conversationId);
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        conversationId,
      );
      try {
        const updatedConversation = await updateConversationRequest(
          token,
          conversationId,
          { is_archived: true },
        );
        if (this.includeArchived) {
          this.conversations = this.conversations.map((conversation) =>
            conversation.id === conversationId
              ? updatedConversation
              : conversation,
          );
        } else {
          this.conversations = this.conversations.filter(
            (conversation) => conversation.id !== conversationId,
          );
        }
        return updatedConversation;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [conversationId]: getErrorMessage(error, "归档会话失败，请稍后重试"),
        };
        return null;
      } finally {
        this.updatingConversationIds = this.updatingConversationIds.filter(
          (id) => id !== conversationId,
        );
      }
    },

    /** 删除是不可恢复操作；只有后端返回成功后，才从本地列表移除会话。 */
    async deleteConversation(conversationId: string): Promise<boolean> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return false;
      }
      if (this.isManagingConversation(conversationId)) {
        return false;
      }

      this.deletingConversationIds.push(conversationId);
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        conversationId,
      );
      try {
        await deleteConversationRequest(token, conversationId);
        this.conversations = this.conversations.filter(
          (conversation) => conversation.id !== conversationId,
        );
        return true;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [conversationId]: getErrorMessage(error, "删除会话失败，请稍后重试"),
        };
        return false;
      } finally {
        this.deletingConversationIds = this.deletingConversationIds.filter(
          (id) => id !== conversationId,
        );
      }
    },

    /** 用户退出登录或切换账号时清空当前标签页内的会话数据。 */
    reset(): void {
      this.$reset();
    },
  },
});
