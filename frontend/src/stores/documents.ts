import { defineStore } from "pinia";

import { ApiError } from "@/api/client";
import type { DocumentResponse } from "@/api/contracts";
import {
  deleteDocument as deleteDocumentRequest,
  listDocuments,
  retryDocument as retryDocumentRequest,
  uploadDocument as uploadDocumentRequest,
} from "@/api/documents";
import { useAuthStore } from "@/stores/auth";

const DEFAULT_POLL_INTERVAL_MS = 2_000;
const ACTIVE_DOCUMENT_STATUSES = new Set(["pending", "processing"]);

type PollingTimer = ReturnType<typeof setTimeout>;

interface DocumentState {
  documentsByKnowledgeBase: Record<string, DocumentResponse[]>;
  loadingKnowledgeBaseIds: string[];
  uploadingKnowledgeBaseIds: string[];
  loadedKnowledgeBaseIds: string[];
  pollingKnowledgeBaseIds: string[];
  retryingDocumentIds: string[];
  deletingDocumentIds: string[];
  deletionRecoveryDocumentIds: string[];
  errorMessages: Record<string, string>;
  actionErrorMessages: Record<string, string>;
}

interface DocumentLocation {
  knowledgeBaseId: string;
  document: DocumentResponse;
}

/**
 * 计时器是浏览器运行资源，不是业务数据，因此不放进 Pinia State，也不会被序列化。
 * WeakMap 以 Store 实例为键，测试创建多个 Pinia 时不会共享轮询任务。
 */
const pollingTimersByStore = new WeakMap<
  object,
  Map<string, PollingTimer>
>();

/** 将已知 API 错误转换为页面可以直接展示的中文消息。 */
function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 以不可变方式删除 Record 中指定键，避免成功后残留旧错误。 */
function withoutKey(
  values: Record<string, string>,
  key: string,
): Record<string, string> {
  const { [key]: _removedValue, ...remainingValues } = values;
  return remainingValues;
}

/** 同一个文档只保留最新响应，同时维持列表原来的展示位置。 */
function upsertDocument(
  documents: DocumentResponse[],
  incomingDocument: DocumentResponse,
): DocumentResponse[] {
  const existingIndex = documents.findIndex(
    (document) => document.id === incomingDocument.id,
  );
  if (existingIndex < 0) {
    return [incomingDocument, ...documents];
  }
  return documents.map((document) =>
    document.id === incomingDocument.id ? incomingDocument : document,
  );
}

/** 找到文档所属知识库，重试和删除成功后据此只更新对应列表。 */
function findDocumentLocation(
  documentsByKnowledgeBase: Record<string, DocumentResponse[]>,
  documentId: string,
): DocumentLocation | null {
  for (const [knowledgeBaseId, documents] of Object.entries(
    documentsByKnowledgeBase,
  )) {
    const document = documents.find((item) => item.id === documentId);
    if (document) {
      return { knowledgeBaseId, document };
    }
  }
  return null;
}

/** 判断列表里是否仍有后台任务没有进入终态。 */
function hasActiveDocument(documents: DocumentResponse[]): boolean {
  return documents.some((document) =>
    ACTIVE_DOCUMENT_STATUSES.has(document.status),
  );
}

/**
 * 后端进入 DELETED 墓碑后会从列表隐藏文档。若对象存储删除失败，前端必须保留
 * 原文档身份，用户才能再次点击删除继续清理。
 */
function reconcileDocuments(
  currentDocuments: DocumentResponse[],
  serverDocuments: DocumentResponse[],
  deletionRecoveryDocumentIds: string[],
): DocumentResponse[] {
  const serverDocumentIds = new Set(
    serverDocuments.map((document) => document.id),
  );
  const recoverableDocuments = currentDocuments.filter(
    (document) =>
      deletionRecoveryDocumentIds.includes(document.id) &&
      !serverDocumentIds.has(document.id),
  );
  return [...serverDocuments, ...recoverableDocuments];
}

/** 获取当前 Store 独享的计时器表，首次使用时再创建。 */
function getPollingTimers(store: object): Map<string, PollingTimer> {
  const existingTimers = pollingTimersByStore.get(store);
  if (existingTimers) {
    return existingTimers;
  }
  const timers = new Map<string, PollingTimer>();
  pollingTimersByStore.set(store, timers);
  return timers;
}

/** 管理文档列表、异步入库状态以及所有单文档操作。 */
export const useDocumentStore = defineStore("documents", {
  state: (): DocumentState => ({
    documentsByKnowledgeBase: {},
    loadingKnowledgeBaseIds: [],
    uploadingKnowledgeBaseIds: [],
    loadedKnowledgeBaseIds: [],
    pollingKnowledgeBaseIds: [],
    retryingDocumentIds: [],
    deletingDocumentIds: [],
    deletionRecoveryDocumentIds: [],
    errorMessages: {},
    actionErrorMessages: {},
  }),
  getters: {
    /** 页面通过知识库 ID 读取对应文档，未加载时返回稳定空数组。 */
    documentsForKnowledgeBase: (state) =>
      (knowledgeBaseId: string): DocumentResponse[] =>
        state.documentsByKnowledgeBase[knowledgeBaseId] ?? [],

    hasLoadedKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.loadedKnowledgeBaseIds.includes(knowledgeBaseId),

    isLoadingKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.loadingKnowledgeBaseIds.includes(knowledgeBaseId),

    isUploadingToKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.uploadingKnowledgeBaseIds.includes(knowledgeBaseId),

    isPollingKnowledgeBase: (state) =>
      (knowledgeBaseId: string): boolean =>
        state.pollingKnowledgeBaseIds.includes(knowledgeBaseId),

    isRetryingDocument: (state) =>
      (documentId: string): boolean =>
        state.retryingDocumentIds.includes(documentId),

    isDeletingDocument: (state) =>
      (documentId: string): boolean =>
        state.deletingDocumentIds.includes(documentId),

    /** 重试和删除互斥，避免较晚返回的响应覆盖较新的文档状态。 */
    isManagingDocument(): (documentId: string) => boolean {
      return (documentId: string): boolean =>
        this.isRetryingDocument(documentId) ||
        this.isDeletingDocument(documentId);
    },
  },
  actions: {
    /** Token 缺失时先停止浏览器资源，再清空上一账号的业务状态。 */
    resetForMissingToken(): void {
      this.reset();
      this.errorMessages = {
        global: "登录状态已失效，请重新登录",
      };
    },

    clearError(knowledgeBaseId: string): void {
      this.errorMessages = withoutKey(this.errorMessages, knowledgeBaseId);
    },

    clearActionError(documentId: string): void {
      this.actionErrorMessages = withoutKey(
        this.actionErrorMessages,
        documentId,
      );
    },

    /**
     * 首次进入知识库时加载列表。请求失败保留旧列表，避免一次网络抖动让页面
     * 突然变空；成功后根据是否存在活动文档决定是否继续轮询。
     */
    async loadDocuments(knowledgeBaseId: string): Promise<void> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return;
      }
      if (this.isLoadingKnowledgeBase(knowledgeBaseId)) {
        return;
      }

      this.loadingKnowledgeBaseIds.push(knowledgeBaseId);
      this.clearError(knowledgeBaseId);
      try {
        const serverDocuments = await listDocuments(token, knowledgeBaseId);
        const currentDocuments =
          this.documentsByKnowledgeBase[knowledgeBaseId] ?? [];
        const documents = reconcileDocuments(
          currentDocuments,
          serverDocuments,
          this.deletionRecoveryDocumentIds,
        );
        this.documentsByKnowledgeBase = {
          ...this.documentsByKnowledgeBase,
          [knowledgeBaseId]: documents,
        };
        if (!this.loadedKnowledgeBaseIds.includes(knowledgeBaseId)) {
          this.loadedKnowledgeBaseIds.push(knowledgeBaseId);
        }
        if (hasActiveDocument(documents)) {
          this.startPolling(knowledgeBaseId);
        } else {
          this.stopPolling(knowledgeBaseId);
        }
      } catch (error) {
        this.errorMessages = {
          ...this.errorMessages,
          [knowledgeBaseId]: getErrorMessage(
            error,
            "加载文档失败，请稍后重试",
          ),
        };
      } finally {
        this.loadingKnowledgeBaseIds = this.loadingKnowledgeBaseIds.filter(
          (id) => id !== knowledgeBaseId,
        );
      }
    },

    /** 上传期间同一知识库只允许一个请求，后端接收后立即开始轮询入库状态。 */
    async uploadDocument(
      knowledgeBaseId: string,
      file: File,
    ): Promise<DocumentResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isUploadingToKnowledgeBase(knowledgeBaseId)) {
        return null;
      }

      this.uploadingKnowledgeBaseIds.push(knowledgeBaseId);
      this.clearError(knowledgeBaseId);
      try {
        const response = await uploadDocumentRequest(
          token,
          knowledgeBaseId,
          file,
        );
        const documents = upsertDocument(
          this.documentsByKnowledgeBase[knowledgeBaseId] ?? [],
          response.document,
        );
        this.documentsByKnowledgeBase = {
          ...this.documentsByKnowledgeBase,
          [knowledgeBaseId]: documents,
        };
        if (!this.loadedKnowledgeBaseIds.includes(knowledgeBaseId)) {
          this.loadedKnowledgeBaseIds.push(knowledgeBaseId);
        }
        this.startPolling(knowledgeBaseId);
        return response.document;
      } catch (error) {
        this.errorMessages = {
          ...this.errorMessages,
          [knowledgeBaseId]: getErrorMessage(
            error,
            "上传文档失败，请稍后重试",
          ),
        };
        return null;
      } finally {
        this.uploadingKnowledgeBaseIds = this.uploadingKnowledgeBaseIds.filter(
          (id) => id !== knowledgeBaseId,
        );
      }
    },

    /** FAILED 文档重试成功后用后端 pending 响应替换旧状态并恢复轮询。 */
    async retryDocument(
      documentId: string,
    ): Promise<DocumentResponse | null> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return null;
      }
      if (this.isManagingDocument(documentId)) {
        return null;
      }

      this.retryingDocumentIds.push(documentId);
      this.clearActionError(documentId);
      try {
        const response = await retryDocumentRequest(token, documentId);
        const knowledgeBaseId = response.document.knowledge_base_id;
        this.documentsByKnowledgeBase = {
          ...this.documentsByKnowledgeBase,
          [knowledgeBaseId]: upsertDocument(
            this.documentsByKnowledgeBase[knowledgeBaseId] ?? [],
            response.document,
          ),
        };
        this.startPolling(knowledgeBaseId);
        return response.document;
      } catch (error) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [documentId]: getErrorMessage(
            error,
            "重试文档入库失败，请稍后再试",
          ),
        };
        return null;
      } finally {
        this.retryingDocumentIds = this.retryingDocumentIds.filter(
          (id) => id !== documentId,
        );
      }
    },

    /**
     * 不做乐观删除：只有后端返回 204 才移除。如果请求失败则保留身份；当上次
     * 删除结果可能已送达但响应丢失时，再次请求得到 404 也视为清理已完成。
     */
    async deleteDocument(documentId: string): Promise<boolean> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return false;
      }
      if (this.isManagingDocument(documentId)) {
        return false;
      }
      const location = findDocumentLocation(
        this.documentsByKnowledgeBase,
        documentId,
      );
      if (!location) {
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [documentId]: "当前页面没有找到该文档，请刷新后重试",
        };
        return false;
      }

      const wasRecovering =
        this.deletionRecoveryDocumentIds.includes(documentId);
      this.deletingDocumentIds.push(documentId);
      this.clearActionError(documentId);
      try {
        await deleteDocumentRequest(token, documentId);
        this.finishDocumentDeletion(location.knowledgeBaseId, documentId);
        return true;
      } catch (error) {
        // 上次请求可能已完成，只是 204 响应在网络中丢失；恢复态 404 等价于已删除。
        if (wasRecovering && error instanceof ApiError && error.status === 404) {
          this.finishDocumentDeletion(location.knowledgeBaseId, documentId);
          return true;
        }
        if (!this.deletionRecoveryDocumentIds.includes(documentId)) {
          this.deletionRecoveryDocumentIds.push(documentId);
        }
        this.actionErrorMessages = {
          ...this.actionErrorMessages,
          [documentId]: getErrorMessage(
            error,
            "删除文档失败，文档已保留，可稍后再次删除",
          ),
        };
        return false;
      } finally {
        this.deletingDocumentIds = this.deletingDocumentIds.filter(
          (id) => id !== documentId,
        );
      }
    },

    /** 删除确认完成后集中清理列表、恢复标记和旧错误。 */
    finishDocumentDeletion(
      knowledgeBaseId: string,
      documentId: string,
    ): void {
      this.documentsByKnowledgeBase = {
        ...this.documentsByKnowledgeBase,
        [knowledgeBaseId]: (
          this.documentsByKnowledgeBase[knowledgeBaseId] ?? []
        ).filter((document) => document.id !== documentId),
      };
      this.deletionRecoveryDocumentIds =
        this.deletionRecoveryDocumentIds.filter((id) => id !== documentId);
      this.clearActionError(documentId);
      if (
        !hasActiveDocument(
          this.documentsByKnowledgeBase[knowledgeBaseId] ?? [],
        )
      ) {
        this.stopPolling(knowledgeBaseId);
      }
    },

    /**
     * 每轮请求结束后才安排下一次 setTimeout，不会像 setInterval 那样在慢网络下
     * 累积并发请求。同一知识库始终最多持有一个计时器。
     */
    startPolling(
      knowledgeBaseId: string,
      intervalMs = DEFAULT_POLL_INTERVAL_MS,
    ): void {
      if (!this.pollingKnowledgeBaseIds.includes(knowledgeBaseId)) {
        this.pollingKnowledgeBaseIds.push(knowledgeBaseId);
      }
      const timers = getPollingTimers(this);
      if (timers.has(knowledgeBaseId)) {
        return;
      }
      const timer = setTimeout(() => {
        timers.delete(knowledgeBaseId);
        void this.pollKnowledgeBase(knowledgeBaseId, intervalMs);
      }, intervalMs);
      timers.set(knowledgeBaseId, timer);
    },

    /** 单次安静刷新不触发页面全屏 loading；临时故障保留旧数据并继续下一轮。 */
    async pollKnowledgeBase(
      knowledgeBaseId: string,
      intervalMs = DEFAULT_POLL_INTERVAL_MS,
    ): Promise<void> {
      if (!this.isPollingKnowledgeBase(knowledgeBaseId)) {
        return;
      }
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return;
      }

      try {
        const serverDocuments = await listDocuments(token, knowledgeBaseId);
        const documents = reconcileDocuments(
          this.documentsByKnowledgeBase[knowledgeBaseId] ?? [],
          serverDocuments,
          this.deletionRecoveryDocumentIds,
        );
        this.documentsByKnowledgeBase = {
          ...this.documentsByKnowledgeBase,
          [knowledgeBaseId]: documents,
        };
        this.clearError(knowledgeBaseId);
        if (!hasActiveDocument(documents)) {
          this.stopPolling(knowledgeBaseId);
          return;
        }
      } catch (error) {
        this.errorMessages = {
          ...this.errorMessages,
          [knowledgeBaseId]: getErrorMessage(
            error,
            "刷新文档状态失败，系统将自动重试",
          ),
        };
      }

      if (this.isPollingKnowledgeBase(knowledgeBaseId)) {
        this.startPolling(knowledgeBaseId, intervalMs);
      }
    },

    /** 停止指定知识库轮询，可在切换详情或全部文档进入终态时调用。 */
    stopPolling(knowledgeBaseId: string): void {
      const timers = getPollingTimers(this);
      const timer = timers.get(knowledgeBaseId);
      if (timer !== undefined) {
        clearTimeout(timer);
        timers.delete(knowledgeBaseId);
      }
      this.pollingKnowledgeBaseIds = this.pollingKnowledgeBaseIds.filter(
        (id) => id !== knowledgeBaseId,
      );
    },

    /** 退出登录和 Store reset 时释放所有浏览器计时器。 */
    stopAllPolling(): void {
      const timers = getPollingTimers(this);
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
      pollingTimersByStore.delete(this);
      this.pollingKnowledgeBaseIds = [];
    },

    /** 清除账号私有状态前必须先释放非响应式计时器。 */
    reset(): void {
      this.stopAllPolling();
      this.$reset();
    },
  },
});
