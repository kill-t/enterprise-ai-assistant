import { defineStore } from "pinia";

import {
  archiveKnowledgeBase as archiveKnowledgeBaseRequest,
  createKnowledgeBase as createKnowledgeBaseRequest,
  listKnowledgeBases,
  type ListKnowledgeBasesOptions,
  updateKnowledgeBase as updateKnowledgeBaseRequest,
} from "@/api/knowledge-bases";
import { ApiError } from "@/api/client";
import type {
  KnowledgeBaseCreateInput,
  KnowledgeBaseResponse,
  KnowledgeBaseUpdateInput,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";

interface KnowledgeBaseState {
  knowledgeBases: KnowledgeBaseResponse[];
  isLoading: boolean;
  isCreating: boolean;
  hasLoaded: boolean;
  includeArchived: boolean;
  errorMessage: string | null;
  updatingKnowledgeBaseIds: string[];
  archivingKnowledgeBaseIds: string[];
  actionErrorMessages: Record<string, string>;
}

/** 将已知 API 错误转换成页面可直接展示的中文消息。 */
function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 清除指定知识库的旧操作错误，避免成功重试后仍然显示过期提示。 */
function clearActionError(
  errorMessages: Record<string, string>,
  knowledgeBaseId: string,
): Record<string, string> {
  const { [knowledgeBaseId]: _removedError, ...remainingErrors } = errorMessages;
  return remainingErrors;
}

/** 管理当前登录用户的知识库列表，HTTP 细节全部委托给 API Client。 */
export const useKnowledgeBaseStore = defineStore("knowledge-bases", {
  state: (): KnowledgeBaseState => ({
    knowledgeBases: [],
    isLoading: false,
    isCreating: false,
    hasLoaded: false,
    includeArchived: false,
    errorMessage: null,
    updatingKnowledgeBaseIds: [],
    archivingKnowledgeBaseIds: [],
    actionErrorMessages: {},
  }),
  getters: {
    /** 判断指定知识库是否正在等待 PATCH 响应。 */
    isUpdatingKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.updatingKnowledgeBaseIds.includes(knowledgeBaseId),

    /** 判断指定知识库是否正在等待归档响应。 */
    isArchivingKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.archivingKnowledgeBaseIds.includes(knowledgeBaseId),

    /** 同一知识库的更新和归档必须互斥，防止响应顺序覆盖较新的状态。 */
    isManagingKnowledgeBase(): (knowledgeBaseId: string) => boolean {
      return (knowledgeBaseId: string): boolean =>
        this.isUpdatingKnowledgeBase(knowledgeBaseId) ||
        this.isArchivingKnowledgeBase(knowledgeBaseId);
    },
  },
  actions: {
    /** 页面已经消费全局错误后可主动清除，避免旧提示影响下一次独立操作。 */
    clearError(): void {
      this.errorMessage = null;
    },

    /** 打开新的单项操作前清除该知识库上一次失败留下的提示。 */
    clearActionErrorMessage(knowledgeBaseId: string): void {
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        knowledgeBaseId,
      );
    },

    /** Token 缺失时清除旧账号数据，并阻止受保护请求发出。 */
    resetForMissingToken(): void {
      this.$reset();
      this.errorMessage = "登录状态已失效，请重新登录";
    },

    /** 加载失败时保留最近一次成功列表，让页面可以提示错误而不突然清空。 */
    async loadKnowledgeBases(
      options: ListKnowledgeBasesOptions = {},
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
        this.knowledgeBases = await listKnowledgeBases(token, options);
        this.hasLoaded = true;
      } catch (error) {
        this.errorMessage = getErrorMessage(
          error,
          "加载知识库失败，请稍后重试",
        );
      } finally {
        this.isLoading = false;
      }
    },

    /** 创建成功后插入当前列表开头，避免为了一个新对象再次读取完整列表。 */
    async createKnowledgeBase(
      input: KnowledgeBaseCreateInput,
    ): Promise<KnowledgeBaseResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isCreating) {
        return null;
      }

      this.isCreating = true;
      this.errorMessage = null;
      try {
        const knowledgeBase = await createKnowledgeBaseRequest(token, input);
        this.knowledgeBases.unshift(knowledgeBase);
        return knowledgeBase;
      } catch (error) {
        this.errorMessage = getErrorMessage(
          error,
          "创建知识库失败，请稍后重试",
        );
        return null;
      } finally {
        this.isCreating = false;
      }
    },

    /** 后端确认更新后才替换列表项；失败时原来的名称和描述保持不变。 */
    async updateKnowledgeBase(
      knowledgeBaseId: string,
      input: KnowledgeBaseUpdateInput,
    ): Promise<KnowledgeBaseResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isManagingKnowledgeBase(knowledgeBaseId)) {
        return null;
      }

      this.updatingKnowledgeBaseIds.push(knowledgeBaseId);
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        knowledgeBaseId,
      );
      try {
        const updatedKnowledgeBase = await updateKnowledgeBaseRequest(
          token,
          knowledgeBaseId,
          input,
        );
        if (updatedKnowledgeBase.is_archived && !this.includeArchived) {
          this.knowledgeBases = this.knowledgeBases.filter(
            (knowledgeBase) => knowledgeBase.id !== knowledgeBaseId,
          );
        } else {
          this.knowledgeBases = this.knowledgeBases.map((knowledgeBase) =>
            knowledgeBase.id === knowledgeBaseId
              ? updatedKnowledgeBase
              : knowledgeBase,
          );
        }
        return updatedKnowledgeBase;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [knowledgeBaseId]: getErrorMessage(
            error,
            "更新知识库失败，请稍后重试",
          ),
        };
        return null;
      } finally {
        this.updatingKnowledgeBaseIds = this.updatingKnowledgeBaseIds.filter(
          (id) => id !== knowledgeBaseId,
        );
      }
    },

    /**
     * DELETE 被后端定义为软归档。204 已确认业务结果，但没有新的 updated_at，
     * 因此活动列表移除目标；包含归档的列表只更新已知的 is_archived 字段。
     */
    async archiveKnowledgeBase(knowledgeBaseId: string): Promise<boolean> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return false;
      }
      if (this.isManagingKnowledgeBase(knowledgeBaseId)) {
        return false;
      }

      this.archivingKnowledgeBaseIds.push(knowledgeBaseId);
      this.actionErrorMessages = clearActionError(
        this.actionErrorMessages,
        knowledgeBaseId,
      );
      try {
        await archiveKnowledgeBaseRequest(token, knowledgeBaseId);
        if (this.includeArchived) {
          this.knowledgeBases = this.knowledgeBases.map((knowledgeBase) =>
            knowledgeBase.id === knowledgeBaseId
              ? { ...knowledgeBase, is_archived: true }
              : knowledgeBase,
          );
        } else {
          this.knowledgeBases = this.knowledgeBases.filter(
            (knowledgeBase) => knowledgeBase.id !== knowledgeBaseId,
          );
        }
        return true;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [knowledgeBaseId]: getErrorMessage(
            error,
            "归档知识库失败，请稍后重试",
          ),
        };
        return false;
      } finally {
        this.archivingKnowledgeBaseIds = this.archivingKnowledgeBaseIds.filter(
          (id) => id !== knowledgeBaseId,
        );
      }
    },

    /** 退出登录或切换账号时清空当前标签页保存的知识库状态。 */
    reset(): void {
      this.$reset();
    },
  },
});
