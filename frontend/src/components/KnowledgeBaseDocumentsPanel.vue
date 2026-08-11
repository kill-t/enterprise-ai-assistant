<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";

import type {
  DocumentResponse,
  DocumentStatus,
  KnowledgeBaseResponse,
} from "@/api/contracts";
import { useDocumentStore } from "@/stores/documents";

const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024;
const SUPPORTED_FILE_EXTENSIONS = new Set(["pdf", "txt", "md", "markdown"]);

const props = defineProps<{ knowledgeBase: KnowledgeBaseResponse }>();
const documentStore = useDocumentStore();
const selectedFile = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const uploadValidationError = ref<string | null>(null);
const deleteTarget = ref<DocumentResponse | null>(null);

const documents = computed(() =>
  documentStore.documentsForKnowledgeBase(props.knowledgeBase.id),
);
const uploadErrorMessage = computed(
  () =>
    uploadValidationError.value ??
    documentStore.errorMessages[props.knowledgeBase.id],
);
const isDeleting = computed(
  () =>
    deleteTarget.value !== null &&
    documentStore.isDeletingDocument(deleteTarget.value.id),
);
const deleteErrorMessage = computed(() =>
  deleteTarget.value
    ? documentStore.actionErrorMessages[deleteTarget.value.id]
    : undefined,
);

/** 后端状态使用稳定英文值，页面在最外层统一转换为中文业务含义。 */
const statusLabels: Record<DocumentStatus, string> = {
  pending: "等待处理",
  processing: "正在入库",
  ready: "可以检索",
  failed: "处理失败",
  deleted: "删除清理中",
};

/** 从文件名读取扩展名；浏览器提供的 MIME 可能为空，不能只依赖 MIME。 */
function fileExtension(filename: string): string {
  const separatorIndex = filename.lastIndexOf(".");
  return separatorIndex < 0
    ? ""
    : filename.slice(separatorIndex + 1).toLowerCase();
}

/**
 * 前端校验提供即时反馈；后端仍会检查类型、大小和内容，
 * 因为客户端提供的文件名与 MIME 不能作为安全边界。
 */
function validateFile(file: File): string | null {
  if (!SUPPORTED_FILE_EXTENSIONS.has(fileExtension(file.name))) {
    return "只支持 PDF、TXT 和 Markdown 文件";
  }
  if (file.size === 0) {
    return "不能上传空文件";
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return "单个文件不能超过 20 MB";
  }
  return null;
}

/** 文件变化时只保存 File 引用，不提前把大文件读进 JavaScript 内存。 */
function handleFileSelection(event: Event): void {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  selectedFile.value = null;
  uploadValidationError.value = null;
  documentStore.clearError(props.knowledgeBase.id);
  if (!file) {
    return;
  }

  const validationError = validateFile(file);
  if (validationError) {
    uploadValidationError.value = validationError;
    // 允许用户修正后再次选择同名文件并触发 change。
    input.value = "";
    return;
  }
  selectedFile.value = file;
}

/** Store 收到后端 202 后会插入 pending 文档，并接管后续轮询。 */
async function uploadSelectedFile(): Promise<void> {
  if (!selectedFile.value || props.knowledgeBase.is_archived) {
    uploadValidationError.value = selectedFile.value
      ? "已归档知识库不能上传新文档"
      : "请先选择要上传的文件";
    return;
  }
  const uploadedDocument = await documentStore.uploadDocument(
    props.knowledgeBase.id,
    selectedFile.value,
  );
  if (!uploadedDocument) {
    return;
  }
  selectedFile.value = null;
  uploadValidationError.value = null;
  if (fileInput.value) {
    fileInput.value.value = "";
  }
}

/** 失败重试由 Store 保证与删除互斥，成功后自动恢复轮询。 */
async function retryDocument(documentId: string): Promise<void> {
  if (!props.knowledgeBase.is_archived) {
    await documentStore.retryDocument(documentId);
  }
}

/** 打开确认框只记录目标，不会立即执行跨 PostgreSQL 与 MinIO 的删除。 */
function openDeleteDialog(document: DocumentResponse): void {
  documentStore.clearActionError(document.id);
  deleteTarget.value = document;
}

/** 请求未结束时确认框不能关闭，避免把关闭误解为取消后端操作。 */
function closeDeleteDialog(): void {
  if (!isDeleting.value) {
    deleteTarget.value = null;
  }
}

/** 只有 Store 确认删除成功后，才关闭对话框并从列表移除文档。 */
async function confirmDeleteDocument(): Promise<void> {
  if (!deleteTarget.value) {
    return;
  }
  if (await documentStore.deleteDocument(deleteTarget.value.id)) {
    closeDeleteDialog();
  }
}

/** 小文件保持 B，较大文件使用 KB/MB，并最多保留一位小数。 */
function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${Number((sizeBytes / 1024).toFixed(1))} KB`;
  }
  return `${Number((sizeBytes / 1024 / 1024).toFixed(1))} MB`;
}

/** 文字始终表达状态，CSS 类只负责辅助视觉区分。 */
function statusClass(status: DocumentStatus): string {
  return `document-status-${status}`;
}

/** 入库中的文档由 Worker 使用，第一版没有取消协议，因此不提供删除按钮。 */
function canDelete(document: DocumentResponse): boolean {
  return document.status === "ready" || document.status === "failed";
}

function isRecoveringDeletion(documentId: string): boolean {
  return documentStore.deletionRecoveryDocumentIds.includes(documentId);
}

// 面板出现时读取文档，切换知识库或离开页面时释放对应轮询。
void documentStore.loadDocuments(props.knowledgeBase.id);
onUnmounted(() => documentStore.stopPolling(props.knowledgeBase.id));
</script>

<template>
  <section
    :id="`document-panel-${knowledgeBase.id}`"
    class="document-panel"
    :data-test="`document-panel-${knowledgeBase.id}`"
    :aria-labelledby="`document-panel-title-${knowledgeBase.id}`"
  >
    <header class="document-panel-header">
      <div>
        <p class="section-label">Knowledge Sources</p>
        <h4 :id="`document-panel-title-${knowledgeBase.id}`">文档管理</h4>
      </div>
      <span v-if="documentStore.isPollingKnowledgeBase(knowledgeBase.id)" class="document-polling-status" role="status">
        正在更新状态…
      </span>
    </header>

    <p v-if="knowledgeBase.is_archived" class="document-archived-notice" role="status">
      该知识库已归档，可以查看已有文档，但不能上传或重试入库。
    </p>

    <div v-else class="document-upload-area">
      <label :for="`document-file-${knowledgeBase.id}`" class="document-file-label">
        选择文档
        <input
          :id="`document-file-${knowledgeBase.id}`"
          ref="fileInput"
          :data-test="`document-file-input-${knowledgeBase.id}`"
          type="file"
          accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown"
          :disabled="documentStore.isUploadingToKnowledgeBase(knowledgeBase.id)"
          @change="handleFileSelection"
        >
      </label>
      <p class="document-upload-help">支持 PDF、TXT、Markdown，单个文件不超过 20 MB。</p>
      <p v-if="selectedFile" class="document-selected-file">
        已选择：{{ selectedFile.name }}（{{ formatFileSize(selectedFile.size) }}）
      </p>
      <p
        v-if="uploadErrorMessage"
        class="document-operation-error"
        data-test="document-upload-error"
        role="alert"
      >
        {{ uploadErrorMessage }}
      </p>
      <button
        class="primary-button document-upload-button"
        :data-test="`upload-document-${knowledgeBase.id}`"
        type="button"
        :disabled="!selectedFile || documentStore.isUploadingToKnowledgeBase(knowledgeBase.id)"
        @click="uploadSelectedFile"
      >
        {{ documentStore.isUploadingToKnowledgeBase(knowledgeBase.id) ? "正在上传…" : "上传并开始入库" }}
      </button>
    </div>

    <p
      v-if="documentStore.isLoadingKnowledgeBase(knowledgeBase.id) && !documentStore.hasLoadedKnowledgeBase(knowledgeBase.id)"
      class="document-list-status"
      role="status"
    >
      正在加载文档…
    </p>
    <div
      v-else-if="documentStore.errorMessages[knowledgeBase.id] && documents.length === 0"
      class="document-load-error"
      role="alert"
    >
      <p>{{ documentStore.errorMessages[knowledgeBase.id] }}</p>
      <button class="text-button" type="button" @click="documentStore.loadDocuments(knowledgeBase.id)">
        重新加载
      </button>
    </div>
    <div
      v-else-if="documentStore.hasLoadedKnowledgeBase(knowledgeBase.id) && documents.length === 0"
      class="document-empty-state"
      data-test="document-empty-state"
    >
      <span aria-hidden="true">▧</span>
      <p>还没有文档，上传第一份资料后系统会自动解析和建立索引。</p>
    </div>

    <ul v-else-if="documents.length > 0" class="document-list" data-test="document-list">
      <li v-for="document in documents" :key="document.id" class="document-card">
        <div class="document-card-main">
          <div class="document-title-row">
            <strong>{{ document.filename }}</strong>
            <span
              class="document-status-badge"
              :class="statusClass(document.status)"
              :data-test="`document-status-${document.id}`"
            >
              {{ statusLabels[document.status] }}
            </span>
            <span v-if="isRecoveringDeletion(document.id)" class="document-recovery-badge">
              删除未完成
            </span>
          </div>
          <p class="document-statistics">
            <span>{{ formatFileSize(document.size_bytes) }}</span>
            <span v-if="document.page_count > 0">{{ document.page_count }} 页</span>
            <span v-if="document.chunk_count > 0">{{ document.chunk_count }} 个 Chunk</span>
          </p>
          <p v-if="document.error_message" class="document-error-detail" role="alert">
            {{ document.error_message }}
          </p>
          <p
            v-if="documentStore.actionErrorMessages[document.id] && deleteTarget?.id !== document.id"
            class="document-operation-error"
            :data-test="`document-action-error-${document.id}`"
            role="alert"
          >
            {{ documentStore.actionErrorMessages[document.id] }}
          </p>
        </div>
        <div class="document-actions">
          <button
            v-if="document.status === 'failed' && !knowledgeBase.is_archived"
            class="text-button"
            :data-test="`retry-document-${document.id}`"
            type="button"
            :disabled="documentStore.isManagingDocument(document.id)"
            @click="retryDocument(document.id)"
          >
            {{ documentStore.isRetryingDocument(document.id) ? "正在重试…" : "重新入库" }}
          </button>
          <button
            v-if="canDelete(document)"
            class="text-button danger-button"
            :data-test="`delete-document-${document.id}`"
            type="button"
            :disabled="documentStore.isManagingDocument(document.id)"
            @click="openDeleteDialog(document)"
          >
            {{ isRecoveringDeletion(document.id) ? "继续删除" : "删除" }}
          </button>
        </div>
      </li>
    </ul>

    <div
      v-if="deleteTarget"
      class="dialog-backdrop document-dialog-backdrop"
      data-test="delete-document-dialog"
      @click.self="closeDeleteDialog"
    >
      <section
        class="management-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-document-title"
      >
        <p class="section-label">不可撤销操作</p>
        <h2 id="delete-document-title">删除文档</h2>
        <p class="dialog-description">
          确认删除“{{ deleteTarget.filename }}”吗？原文件、Chunk 和向量都会被清理，历史回答仍保留引用快照。
        </p>
        <p v-if="deleteErrorMessage" class="dialog-error" role="alert">
          {{ deleteErrorMessage }}
        </p>
        <div class="dialog-actions">
          <button class="secondary-button" type="button" :disabled="isDeleting" @click="closeDeleteDialog">
            取消
          </button>
          <button
            class="danger-confirm-button"
            data-test="confirm-delete-document"
            type="button"
            :disabled="isDeleting"
            @click="confirmDeleteDocument"
          >
            {{ isDeleting ? "正在删除…" : isRecoveringDeletion(deleteTarget.id) ? "继续清理" : "确认删除" }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>
