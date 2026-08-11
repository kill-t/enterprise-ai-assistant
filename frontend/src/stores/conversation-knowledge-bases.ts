import { defineStore } from "pinia";

import {
  getConversationKnowledgeBases,
  replaceConversationKnowledgeBases,
} from "@/api/conversation-knowledge-bases";
import { ApiError } from "@/api/client";
import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";

const MAX_BOUND_KNOWLEDGE_BASES = 5;

interface ConversationKnowledgeBaseState {
  conversationId: string | null;
  boundKnowledgeBases: KnowledgeBaseResponse[];
  selectedKnowledgeBaseIds: string[];
  isLoading: boolean;
  hasLoaded: boolean;
  isSaving: boolean;
  errorMessage: string | null;
  selectionErrorMessage: string | null;
  saveErrorMessage: string | null;
  loadRequestVersion: number;
  saveRequestVersion: number;
}

/** 已知 API 错误优先展示后端中文信息，未知异常使用稳定兜底文本。 */
function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 集合排序后比较，使勾选顺序变化不会被误判为绑定内容变化。 */
function sameIdSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  const sortedLeft = [...left].sort();
  const sortedRight = [...right].sort();
  return sortedLeft.every((id, index) => id === sortedRight[index]);
}

/** 管理一个会话的服务端绑定快照和尚未保存的多选草稿。 */
export const useConversationKnowledgeBaseStore = defineStore(
  "conversation-knowledge-bases",
  {
    state: (): ConversationKnowledgeBaseState => ({
      conversationId: null,
      boundKnowledgeBases: [],
      selectedKnowledgeBaseIds: [],
      isLoading: false,
      hasLoaded: false,
      isSaving: false,
      errorMessage: null,
      selectionErrorMessage: null,
      saveErrorMessage: null,
      loadRequestVersion: 0,
      saveRequestVersion: 0,
    }),
    getters: {
      /** 页面摘要展示的是已持久化绑定，不把未保存草稿伪装成后端状态。 */
      boundKnowledgeBaseIds: (state): string[] =>
        state.boundKnowledgeBases.map((knowledgeBase) => knowledgeBase.id),

      /** 比较集合而非数组顺序，避免用户取消再勾选后产生无意义保存。 */
      hasUnsavedChanges(): boolean {
        return !sameIdSet(
          this.boundKnowledgeBaseIds,
          this.selectedKnowledgeBaseIds,
        );
      },

      isSelected: (state) =>
        (knowledgeBaseId: string): boolean =>
          state.selectedKnowledgeBaseIds.includes(knowledgeBaseId),

      /** 已归档旧绑定不能再次提交，用户需要先明确取消它。 */
      hasArchivedSelection: (state): boolean => {
        const archivedIds = new Set(
          state.boundKnowledgeBases
            .filter((knowledgeBase) => knowledgeBase.is_archived)
            .map((knowledgeBase) => knowledgeBase.id),
        );
        return state.selectedKnowledgeBaseIds.some((id) => archivedIds.has(id));
      },
    },
    actions: {
      /**
       * 手动清理而不是直接 $reset：先递增版本，使 reset 前发出的迟到请求永远失效。
       */
      reset(): void {
        this.loadRequestVersion += 1;
        this.saveRequestVersion += 1;
        this.conversationId = null;
        this.boundKnowledgeBases = [];
        this.selectedKnowledgeBaseIds = [];
        this.isLoading = false;
        this.hasLoaded = false;
        this.isSaving = false;
        this.errorMessage = null;
        this.selectionErrorMessage = null;
        this.saveErrorMessage = null;
      },

      resetForMissingToken(): void {
        this.reset();
        this.errorMessage = "登录状态已失效，请重新登录";
      },

      /** 切换会话时立即清空旧快照，并忽略较晚返回的旧会话响应。 */
      async load(conversationId: string): Promise<void> {
        const token = useAuthStore().token;
        if (!token) {
          this.resetForMissingToken();
          return;
        }

        const requestVersion = this.loadRequestVersion + 1;
        this.loadRequestVersion = requestVersion;
        if (this.conversationId !== conversationId) {
          // 新会话出现后，旧会话尚未完成的保存也不能继续写入当前状态。
          this.saveRequestVersion += 1;
          this.conversationId = conversationId;
          this.boundKnowledgeBases = [];
          this.selectedKnowledgeBaseIds = [];
          this.hasLoaded = false;
          this.isSaving = false;
          this.selectionErrorMessage = null;
          this.saveErrorMessage = null;
        }
        this.isLoading = true;
        this.errorMessage = null;

        try {
          const response = await getConversationKnowledgeBases(
            token,
            conversationId,
          );
          if (
            this.loadRequestVersion !== requestVersion ||
            this.conversationId !== conversationId
          ) {
            return;
          }
          this.boundKnowledgeBases = response.knowledge_bases;
          this.selectedKnowledgeBaseIds = response.knowledge_bases.map(
            (knowledgeBase) => knowledgeBase.id,
          );
          this.hasLoaded = true;
        } catch (error) {
          if (
            this.loadRequestVersion === requestVersion &&
            this.conversationId === conversationId
          ) {
            this.errorMessage = errorMessage(
              error,
              "加载会话知识库绑定失败，请稍后重试",
            );
          }
        } finally {
          if (
            this.loadRequestVersion === requestVersion &&
            this.conversationId === conversationId
          ) {
            this.isLoading = false;
          }
        }
      },

      /** 修改本地草稿；达到上限时保持原集合，不发送任何后端请求。 */
      toggleSelection(knowledgeBaseId: string): void {
        this.selectionErrorMessage = null;
        this.saveErrorMessage = null;
        if (this.selectedKnowledgeBaseIds.includes(knowledgeBaseId)) {
          this.selectedKnowledgeBaseIds = this.selectedKnowledgeBaseIds.filter(
            (id) => id !== knowledgeBaseId,
          );
          return;
        }
        if (this.selectedKnowledgeBaseIds.length >= MAX_BOUND_KNOWLEDGE_BASES) {
          this.selectionErrorMessage = "每个会话最多选择 5 个知识库";
          return;
        }
        this.selectedKnowledgeBaseIds = [
          ...this.selectedKnowledgeBaseIds,
          knowledgeBaseId,
        ];
      },

      /** 取消编辑只恢复最近一次服务端快照，不需要重新发 GET。 */
      discardChanges(): void {
        this.selectedKnowledgeBaseIds = this.boundKnowledgeBases.map(
          (knowledgeBase) => knowledgeBase.id,
        );
        this.selectionErrorMessage = null;
        this.saveErrorMessage = null;
      },

      /** PUT 原子替换完整集合；失败时保留草稿供用户修正或重试。 */
      async save(): Promise<boolean> {
        const token = useAuthStore().token;
        const conversationId = this.conversationId;
        if (!token || !conversationId) {
          this.resetForMissingToken();
          return false;
        }
        if (this.isSaving) {
          return false;
        }
        this.selectionErrorMessage = null;
        this.saveErrorMessage = null;
        if (this.hasArchivedSelection) {
          this.selectionErrorMessage =
            "请先取消已归档知识库，它们不会参与后续检索";
          return false;
        }

        const requestVersion = this.saveRequestVersion + 1;
        this.saveRequestVersion = requestVersion;
        this.isSaving = true;
        try {
          const response = await replaceConversationKnowledgeBases(
            token,
            conversationId,
            { knowledge_base_ids: [...this.selectedKnowledgeBaseIds] },
          );
          if (
            this.saveRequestVersion !== requestVersion ||
            this.conversationId !== conversationId
          ) {
            return false;
          }
          this.boundKnowledgeBases = response.knowledge_bases;
          this.selectedKnowledgeBaseIds = response.knowledge_bases.map(
            (knowledgeBase) => knowledgeBase.id,
          );
          return true;
        } catch (error) {
          if (
            this.saveRequestVersion === requestVersion &&
            this.conversationId === conversationId
          ) {
            this.saveErrorMessage = errorMessage(
              error,
              "保存会话知识库失败，请稍后重试",
            );
          }
          return false;
        } finally {
          if (
            this.saveRequestVersion === requestVersion &&
            this.conversationId === conversationId
          ) {
            this.isSaving = false;
          }
        }
      },
    },
  },
);
