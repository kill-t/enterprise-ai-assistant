<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useKnowledgeBaseStore } from "@/stores/knowledge-bases";
import { resetUserDataStores } from "@/stores/user-data";

const MAX_NAME_LENGTH = 255;
const MAX_DESCRIPTION_LENGTH = 4000;

const authStore = useAuthStore();
const knowledgeBaseStore = useKnowledgeBaseStore();
const router = useRouter();
const name = ref("");
const description = ref("");
const formErrorMessage = ref<string | null>(null);
const createSuccessMessage = ref<string | null>(null);
const editTarget = ref<KnowledgeBaseResponse | null>(null);
const editName = ref("");
const editDescription = ref("");
const editValidationError = ref<string | null>(null);
const editNameInput = ref<HTMLInputElement | null>(null);
const archiveTarget = ref<KnowledgeBaseResponse | null>(null);

/** 当前编辑目标是否仍在等待 PATCH，期间对话框字段和关闭操作都要禁用。 */
const isEditing = computed(
  () =>
    editTarget.value !== null &&
    knowledgeBaseStore.isUpdatingKnowledgeBase(editTarget.value.id),
);

/** 编辑错误优先显示本地校验，其次显示 Store 保存的后端单项错误。 */
const editErrorMessage = computed(() => {
  if (editValidationError.value) {
    return editValidationError.value;
  }
  return editTarget.value
    ? knowledgeBaseStore.actionErrorMessages[editTarget.value.id]
    : undefined;
});

/** 当前归档确认是否仍在等待 DELETE 的 204 响应。 */
const isArchiving = computed(
  () =>
    archiveTarget.value !== null &&
    knowledgeBaseStore.isArchivingKnowledgeBase(archiveTarget.value.id),
);

/** 将归档失败原因绑定到当前确认框，而不是显示成列表级错误。 */
const archiveErrorMessage = computed(() =>
  archiveTarget.value
    ? knowledgeBaseStore.actionErrorMessages[archiveTarget.value.id]
    : undefined,
);

/** 按当前筛选重新读取知识库；筛选参数仍由 Store 统一保存。 */
async function reloadKnowledgeBases(
  includeArchived = knowledgeBaseStore.includeArchived,
): Promise<void> {
  await knowledgeBaseStore.loadKnowledgeBases(
    includeArchived ? { includeArchived: true } : {},
  );
}

/** 切换归档筛选时重新请求后端，确保列表继续受到用户所有权过滤。 */
async function toggleArchivedKnowledgeBases(): Promise<void> {
  await reloadKnowledgeBases(!knowledgeBaseStore.includeArchived);
}

/** 用户继续输入后清理上一轮表单反馈，但不改变已经成功加载的列表。 */
function clearFormFeedback(): void {
  formErrorMessage.value = null;
  createSuccessMessage.value = null;
}

/** 打开编辑框时复制服务端已保存值，取消编辑不会修改列表对象。 */
function openEditDialog(knowledgeBase: KnowledgeBaseResponse): void {
  knowledgeBaseStore.clearActionErrorMessage(knowledgeBase.id);
  editTarget.value = knowledgeBase;
  editName.value = knowledgeBase.name;
  editDescription.value = knowledgeBase.description ?? "";
  editValidationError.value = null;
  void nextTick(() => editNameInput.value?.focus());
}

/** 用户继续修改字段时清除上一轮本地校验错误。 */
function clearEditValidationError(): void {
  editValidationError.value = null;
}

/** PATCH 未结束时禁止关闭，避免用户误以为保存请求已经被取消。 */
function closeEditDialog(): void {
  if (isEditing.value) {
    return;
  }
  editTarget.value = null;
  editName.value = "";
  editDescription.value = "";
  editValidationError.value = null;
}

/** 校验编辑副本，并在后端确认成功后才关闭对话框和展示新卡片。 */
async function submitKnowledgeBaseEdit(): Promise<void> {
  const target = editTarget.value;
  if (!target) {
    return;
  }

  const normalizedName = editName.value.trim();
  const normalizedDescription = editDescription.value.trim();
  editValidationError.value = null;
  if (!normalizedName) {
    editValidationError.value = "知识库名称不能为空";
    return;
  }
  if (normalizedName.length > MAX_NAME_LENGTH) {
    editValidationError.value =
      `知识库名称不能超过 ${MAX_NAME_LENGTH} 个字符`;
    return;
  }
  if (normalizedDescription.length > MAX_DESCRIPTION_LENGTH) {
    editValidationError.value =
      `知识库描述不能超过 ${MAX_DESCRIPTION_LENGTH} 个字符`;
    return;
  }

  const updatedKnowledgeBase = await knowledgeBaseStore.updateKnowledgeBase(
    target.id,
    {
      name: normalizedName,
      description: normalizedDescription || null,
    },
  );
  if (updatedKnowledgeBase) {
    closeEditDialog();
  }
}

/** 打开归档确认框时只记录目标，不会立即产生 DELETE 请求。 */
function openArchiveDialog(knowledgeBase: KnowledgeBaseResponse): void {
  knowledgeBaseStore.clearActionErrorMessage(knowledgeBase.id);
  archiveTarget.value = knowledgeBase;
}

/** 归档请求未结束时保持确认框，等待后端给出明确终态。 */
function closeArchiveDialog(): void {
  if (isArchiving.value) {
    return;
  }
  archiveTarget.value = null;
}

/** 只有确认框中的二次操作才会真正调用软归档接口。 */
async function confirmArchiveKnowledgeBase(): Promise<void> {
  const target = archiveTarget.value;
  if (!target) {
    return;
  }
  const archived = await knowledgeBaseStore.archiveKnowledgeBase(target.id);
  if (archived) {
    closeArchiveDialog();
  }
}

/** 恢复是 PATCH 状态转换；成功响应会直接替换归档卡片。 */
async function restoreKnowledgeBase(knowledgeBaseId: string): Promise<void> {
  await knowledgeBaseStore.updateKnowledgeBase(knowledgeBaseId, {
    is_archived: false,
  });
}

/**
 * 前端先提供即时中文校验；后端 Pydantic 仍会再次执行长度和业务唯一性校验。
 */
async function submitKnowledgeBase(): Promise<void> {
  const normalizedName = name.value.trim();
  const normalizedDescription = description.value.trim();
  formErrorMessage.value = null;
  createSuccessMessage.value = null;
  knowledgeBaseStore.clearError();

  if (!normalizedName) {
    formErrorMessage.value = "知识库名称不能为空";
    return;
  }
  if (normalizedName.length > MAX_NAME_LENGTH) {
    formErrorMessage.value = `知识库名称不能超过 ${MAX_NAME_LENGTH} 个字符`;
    return;
  }
  if (normalizedDescription.length > MAX_DESCRIPTION_LENGTH) {
    formErrorMessage.value =
      `知识库描述不能超过 ${MAX_DESCRIPTION_LENGTH} 个字符`;
    return;
  }

  const createdKnowledgeBase = await knowledgeBaseStore.createKnowledgeBase({
    name: normalizedName,
    description: normalizedDescription || null,
  });
  if (!createdKnowledgeBase) {
    formErrorMessage.value =
      knowledgeBaseStore.errorMessage ?? "创建知识库失败，请稍后重试";
    // 错误已经移动到表单附近展示，避免列表区域再次重复同一条提示。
    knowledgeBaseStore.clearError();
    return;
  }

  name.value = "";
  description.value = "";
  createSuccessMessage.value = `知识库创建成功：${createdKnowledgeBase.name}`;
}

/** 用本地日期格式展示后端时间，原始 ISO 值仍放在 datetime 属性中。 */
function formatUpdatedAt(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(value));
}

/** 退出前集中清除所有用户私有 Store，再替换到登录页。 */
async function logout(): Promise<void> {
  resetUserDataStores();
  authStore.logout();
  await router.replace({ name: "login" });
}

onMounted(() => {
  if (!knowledgeBaseStore.hasLoaded) {
    void reloadKnowledgeBases();
  }
});
</script>

<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <RouterLink
        class="workspace-brand"
        :to="{ name: 'home' }"
        aria-label="Enterprise AI Assistant 首页"
      >
        <span class="brand-mark small" aria-hidden="true">EA</span>
        <span>Enterprise AI Assistant</span>
      </RouterLink>
      <div class="account-area">
        <span>{{ authStore.user?.email ?? "已登录用户" }}</span>
        <button class="secondary-button" type="button" @click="logout">
          退出登录
        </button>
      </div>
    </header>

    <main class="workspace-main knowledge-base-main">
      <p v-if="authStore.sessionError" class="session-warning" role="status">
        {{ authStore.sessionError }}
      </p>
      <RouterLink class="back-link" :to="{ name: 'home' }">
        ← 返回会话工作台
      </RouterLink>

      <section class="workspace-intro knowledge-base-intro" aria-labelledby="knowledge-base-title">
        <div>
          <p class="eyebrow">Enterprise Knowledge</p>
          <h1 id="knowledge-base-title">管理企业知识来源</h1>
          <p>
            每个知识库只属于当前账号。原文件、处理任务、Chunk 和向量都由后端统一管理。
          </p>
        </div>
      </section>

      <div class="knowledge-base-layout">
        <section class="knowledge-create-panel" aria-labelledby="create-knowledge-base-title">
          <p class="section-label">新知识库</p>
          <h2 id="create-knowledge-base-title">创建资料空间</h2>
          <p class="knowledge-panel-description">
            先创建知识库，下一步再向其中上传 PDF、TXT 或 Markdown 文档。
          </p>

          <form
            class="knowledge-create-form"
            data-test="knowledge-base-create-form"
            @submit.prevent="submitKnowledgeBase"
          >
            <label for="knowledge-base-name">
              知识库名称
              <input
                id="knowledge-base-name"
                v-model="name"
                data-test="knowledge-base-name"
                type="text"
                :maxlength="MAX_NAME_LENGTH"
                autocomplete="off"
                :disabled="knowledgeBaseStore.isCreating"
                placeholder="例如：项目交付资料"
                @input="clearFormFeedback"
              >
            </label>
            <label for="knowledge-base-description">
              描述（可选）
              <textarea
                id="knowledge-base-description"
                v-model="description"
                data-test="knowledge-base-description"
                rows="6"
                :maxlength="MAX_DESCRIPTION_LENGTH"
                :disabled="knowledgeBaseStore.isCreating"
                placeholder="说明这个知识库保存哪些资料、供谁使用"
                @input="clearFormFeedback"
              />
            </label>
            <p class="knowledge-description-count">
              {{ description.length }} / {{ MAX_DESCRIPTION_LENGTH }}
            </p>
            <p
              v-if="formErrorMessage"
              class="form-error"
              data-test="knowledge-base-form-error"
              role="alert"
            >
              {{ formErrorMessage }}
            </p>
            <p
              v-if="createSuccessMessage"
              class="knowledge-create-success"
              data-test="knowledge-base-create-success"
              role="status"
            >
              {{ createSuccessMessage }}
            </p>
            <button
              class="primary-button create-knowledge-base-button"
              data-test="create-knowledge-base"
              type="submit"
              :disabled="knowledgeBaseStore.isCreating"
            >
              {{ knowledgeBaseStore.isCreating ? "正在创建…" : "创建知识库" }}
            </button>
          </form>
        </section>

        <section class="knowledge-list-panel" aria-labelledby="knowledge-base-list-title">
          <div class="conversation-panel-heading">
            <div>
              <p class="section-label">我的资料空间</p>
              <h2 id="knowledge-base-list-title">知识库列表</h2>
            </div>
            <div class="conversation-panel-controls">
              <button
                class="filter-button"
                data-test="toggle-archived-knowledge-bases"
                type="button"
                :aria-pressed="knowledgeBaseStore.includeArchived"
                :disabled="knowledgeBaseStore.isLoading"
                @click="toggleArchivedKnowledgeBases"
              >
                {{ knowledgeBaseStore.includeArchived ? "仅看使用中" : "显示已归档" }}
              </button>
              <span v-if="knowledgeBaseStore.hasLoaded" class="conversation-count">
                {{ knowledgeBaseStore.knowledgeBases.length }} 个知识库
              </span>
            </div>
          </div>

          <p
            v-if="knowledgeBaseStore.isLoading && !knowledgeBaseStore.hasLoaded"
            class="conversation-status"
            data-test="knowledge-base-loading-state"
            role="status"
          >
            正在加载知识库…
          </p>

          <div v-if="knowledgeBaseStore.errorMessage" class="conversation-error" role="alert">
            <p>{{ knowledgeBaseStore.errorMessage }}</p>
            <button
              class="text-button"
              data-test="retry-load-knowledge-bases"
              type="button"
              :disabled="knowledgeBaseStore.isLoading"
              @click="reloadKnowledgeBases()"
            >
              重新加载
            </button>
          </div>

          <div
            v-if="!knowledgeBaseStore.isLoading && knowledgeBaseStore.hasLoaded && knowledgeBaseStore.knowledgeBases.length === 0"
            class="empty-state knowledge-empty-state"
            data-test="knowledge-base-empty-state"
          >
            <span class="empty-state-mark" aria-hidden="true">▤</span>
            <h3>还没有知识库</h3>
            <p>在左侧填写名称和描述，创建第一个企业资料空间。</p>
          </div>

          <ul
            v-else-if="knowledgeBaseStore.knowledgeBases.length > 0"
            class="knowledge-base-list"
            data-test="knowledge-base-list"
          >
            <li v-for="knowledgeBase in knowledgeBaseStore.knowledgeBases" :key="knowledgeBase.id">
              <article class="knowledge-base-card">
                <header class="knowledge-base-card-header">
                  <div>
                    <h3>{{ knowledgeBase.name }}</h3>
                    <span
                      v-if="knowledgeBase.is_archived"
                      class="archived-badge"
                      data-test="archived-knowledge-base-badge"
                    >
                      已归档
                    </span>
                    <span v-else class="knowledge-active-badge">使用中</span>
                  </div>
                  <time :datetime="knowledgeBase.updated_at">
                    更新于 {{ formatUpdatedAt(knowledgeBase.updated_at) }}
                  </time>
                </header>
                <p class="knowledge-base-description">
                  {{ knowledgeBase.description ?? "暂无描述" }}
                </p>
                <dl class="knowledge-base-metadata">
                  <div>
                    <dt>Embedding</dt>
                    <dd>{{ knowledgeBase.embedding_model }}</dd>
                  </div>
                  <div>
                    <dt>向量维度</dt>
                    <dd>{{ knowledgeBase.embedding_dimension }} 维</dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>{{ knowledgeBase.embedding_provider }}</dd>
                  </div>
                </dl>
                <div class="knowledge-base-actions" aria-label="知识库管理操作">
                  <button
                    class="text-button"
                    :data-test="`edit-knowledge-base-${knowledgeBase.id}`"
                    type="button"
                    :disabled="knowledgeBaseStore.isManagingKnowledgeBase(knowledgeBase.id)"
                    @click="openEditDialog(knowledgeBase)"
                  >
                    {{ knowledgeBaseStore.isUpdatingKnowledgeBase(knowledgeBase.id) ? "处理中…" : "编辑" }}
                  </button>
                  <button
                    v-if="!knowledgeBase.is_archived"
                    class="text-button danger-button"
                    :data-test="`archive-knowledge-base-${knowledgeBase.id}`"
                    type="button"
                    :disabled="knowledgeBaseStore.isManagingKnowledgeBase(knowledgeBase.id)"
                    @click="openArchiveDialog(knowledgeBase)"
                  >
                    {{ knowledgeBaseStore.isArchivingKnowledgeBase(knowledgeBase.id) ? "归档中…" : "归档" }}
                  </button>
                  <button
                    v-else
                    class="text-button restore-knowledge-base-button"
                    :data-test="`restore-knowledge-base-${knowledgeBase.id}`"
                    type="button"
                    :disabled="knowledgeBaseStore.isManagingKnowledgeBase(knowledgeBase.id)"
                    @click="restoreKnowledgeBase(knowledgeBase.id)"
                  >
                    {{ knowledgeBaseStore.isUpdatingKnowledgeBase(knowledgeBase.id) ? "恢复中…" : "恢复使用" }}
                  </button>
                </div>
                <p
                  v-if="knowledgeBaseStore.actionErrorMessages[knowledgeBase.id] && editTarget?.id !== knowledgeBase.id && archiveTarget?.id !== knowledgeBase.id"
                  class="knowledge-base-action-error"
                  role="alert"
                >
                  {{ knowledgeBaseStore.actionErrorMessages[knowledgeBase.id] }}
                </p>
              </article>
            </li>
          </ul>
        </section>
      </div>
    </main>

    <div
      v-if="editTarget"
      class="dialog-backdrop"
      data-test="edit-knowledge-base-dialog"
      @click.self="closeEditDialog"
    >
      <section
        class="management-dialog knowledge-edit-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-knowledge-base-title"
      >
        <form data-test="edit-knowledge-base-form" @submit.prevent="submitKnowledgeBaseEdit">
          <p class="section-label">知识库管理</p>
          <h2 id="edit-knowledge-base-title">编辑知识库</h2>
          <label class="dialog-field" for="edit-knowledge-base-name">
            知识库名称
            <input
              id="edit-knowledge-base-name"
              ref="editNameInput"
              v-model="editName"
              data-test="edit-knowledge-base-name"
              type="text"
              :maxlength="MAX_NAME_LENGTH"
              autocomplete="off"
              :disabled="isEditing"
              @input="clearEditValidationError"
            >
          </label>
          <label class="dialog-field" for="edit-knowledge-base-description">
            描述（可选）
            <textarea
              id="edit-knowledge-base-description"
              v-model="editDescription"
              data-test="edit-knowledge-base-description"
              rows="5"
              :maxlength="MAX_DESCRIPTION_LENGTH"
              :disabled="isEditing"
              @input="clearEditValidationError"
            />
          </label>
          <p class="knowledge-description-count">
            {{ editDescription.length }} / {{ MAX_DESCRIPTION_LENGTH }}
          </p>
          <p
            v-if="editErrorMessage"
            class="dialog-error"
            data-test="edit-knowledge-base-error"
            role="alert"
          >
            {{ editErrorMessage }}
          </p>
          <div class="dialog-actions">
            <button class="secondary-button" type="button" :disabled="isEditing" @click="closeEditDialog">
              取消
            </button>
            <button class="primary-button" type="submit" :disabled="isEditing">
              {{ isEditing ? "正在保存…" : "保存修改" }}
            </button>
          </div>
        </form>
      </section>
    </div>

    <div
      v-if="archiveTarget"
      class="dialog-backdrop"
      data-test="archive-knowledge-base-dialog"
      @click.self="closeArchiveDialog"
    >
      <section
        class="management-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="archive-knowledge-base-title"
      >
        <p class="section-label">可恢复操作</p>
        <h2 id="archive-knowledge-base-title">归档知识库</h2>
        <p class="dialog-description">
          归档“{{ archiveTarget.name }}”后，它将停止参与检索。系统不会物理删除文档，之后仍可从归档列表恢复。
        </p>
        <p v-if="archiveErrorMessage" class="dialog-error" role="alert">
          {{ archiveErrorMessage }}
        </p>
        <div class="dialog-actions">
          <button class="secondary-button" type="button" :disabled="isArchiving" @click="closeArchiveDialog">
            取消
          </button>
          <button
            class="danger-confirm-button"
            data-test="confirm-archive-knowledge-base"
            type="button"
            :disabled="isArchiving"
            @click="confirmArchiveKnowledgeBase"
          >
            {{ isArchiving ? "正在归档…" : "确认归档" }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
