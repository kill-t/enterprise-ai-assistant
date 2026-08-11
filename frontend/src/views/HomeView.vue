<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import type { ConversationResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";
import { resetUserDataStores } from "@/stores/user-data";

const DEFAULT_CONVERSATION_TITLE = "新建会话";

const authStore = useAuthStore();
const conversationStore = useConversationStore();
const router = useRouter();
const renameConversationId = ref<string | null>(null);
const renameTitle = ref("");
const deleteTargetConversation = ref<ConversationResponse | null>(null);
const renameInput = ref<HTMLInputElement | null>(null);

/** 当前打开重命名框的会话是否仍在等待后端更新，供表单禁用按钮使用。 */
const isRenaming = computed(
  () =>
    renameConversationId.value !== null &&
    conversationStore.isUpdatingConversation(renameConversationId.value),
);

/** 将 Store 中按会话 ID 保存的错误取到当前重命名表单中展示。 */
const renameErrorMessage = computed(() =>
  renameConversationId.value
    ? conversationStore.actionErrorMessages[renameConversationId.value]
    : undefined,
);

/** 当前打开删除确认框的会话是否仍在等待后端删除。 */
const isDeleting = computed(
  () =>
    deleteTargetConversation.value !== null &&
    conversationStore.isDeletingConversation(deleteTargetConversation.value.id),
);

/** 将 Store 中按会话 ID 保存的错误取到当前删除确认框中展示。 */
const deleteErrorMessage = computed(() =>
  deleteTargetConversation.value
    ? conversationStore.actionErrorMessages[deleteTargetConversation.value.id]
    : undefined,
);

/** 读取列表时只在需要包含归档会话时添加查询条件，默认行为仍由后端定义。 */
async function reloadConversations(includeArchived = conversationStore.includeArchived): Promise<void> {
  await conversationStore.loadConversations(
    includeArchived ? { includeArchived: true } : {},
  );
}

/** 用可读的本地时间展示后端 ISO 时间，原始值仍保留在 time 的 datetime 属性中。 */
function formatUpdatedAt(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 使用第一版默认标题创建会话；用户可随后从管理操作中调整标题。 */
async function createNewConversation(): Promise<void> {
  await conversationStore.createConversation({
    title: DEFAULT_CONVERSATION_TITLE,
  });
}

/** 切换“包含已归档”筛选后重新请求服务端，保证结果仍受用户所有权约束。 */
async function toggleArchivedConversations(): Promise<void> {
  await reloadConversations(!conversationStore.includeArchived);
}

/** 打开重命名框时复制标题，取消编辑不会影响列表中的已保存标题。 */
function openRenameDialog(conversation: ConversationResponse): void {
  renameConversationId.value = conversation.id;
  renameTitle.value = conversation.title;
  void nextTick(() => renameInput.value?.focus());
}

/** 请求未结束时不允许关闭重命名框，避免用户误以为本次保存已经取消。 */
function closeRenameDialog(): void {
  if (isRenaming.value) {
    return;
  }
  renameConversationId.value = null;
  renameTitle.value = "";
}

/** 只有 Store 确认更新成功后才关闭对话框；失败错误由 Store 保留并显示。 */
async function submitRename(): Promise<void> {
  const conversationId = renameConversationId.value;
  if (!conversationId) {
    return;
  }

  const updatedConversation = await conversationStore.renameConversation(
    conversationId,
    renameTitle.value,
  );
  if (updatedConversation) {
    closeRenameDialog();
  }
}

/** 归档不需要二次确认，但同一会话的按钮在请求期间由 Store 统一禁用。 */
async function archiveConversation(conversationId: string): Promise<void> {
  await conversationStore.archiveConversation(conversationId);
}

/** 删除前仅记录目标会话并打开确认框；此时尚未产生任何删除请求。 */
function openDeleteDialog(conversation: ConversationResponse): void {
  deleteTargetConversation.value = conversation;
}

/** 请求未结束时不允许关闭删除框，避免用户对最终结果产生误解。 */
function closeDeleteDialog(): void {
  if (isDeleting.value) {
    return;
  }
  deleteTargetConversation.value = null;
}

/** 删除是不可恢复操作，只有用户在确认框中再次点击后才真正调用 Store。 */
async function confirmDelete(): Promise<void> {
  const conversation = deleteTargetConversation.value;
  if (!conversation) {
    return;
  }

  const isDeleted = await conversationStore.deleteConversation(conversation.id);
  if (isDeleted) {
    closeDeleteDialog();
  }
}

/** 清理所有当前用户内存状态并替换历史记录，避免后退回受保护页面。 */
async function logout(): Promise<void> {
  resetUserDataStores();
  authStore.logout();
  await router.replace({ name: "login" });
}

onMounted(() => {
  if (!conversationStore.hasLoaded) {
    void reloadConversations();
  }
});
</script>

<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <RouterLink class="workspace-brand" :to="{ name: 'home' }" aria-label="Enterprise AI Assistant 首页">
        <span class="brand-mark small" aria-hidden="true">EA</span>
        <span>Enterprise AI Assistant</span>
      </RouterLink>
      <div class="account-area">
        <span>{{ authStore.user?.email ?? "已登录用户" }}</span>
        <button class="secondary-button" type="button" @click="logout">退出登录</button>
      </div>
    </header>

    <main class="workspace-main">
      <p v-if="authStore.sessionError" class="session-warning" role="status">
        {{ authStore.sessionError }}
      </p>

      <section class="workspace-intro" aria-labelledby="workspace-title">
        <div>
          <p class="eyebrow">Conversation Workspace</p>
          <h1 id="workspace-title">从一个清晰的会话开始</h1>
          <p>
            会话保存你的问题、模型回复和后续可追溯的工作流。每个会话仅属于当前登录用户。
          </p>
        </div>
        <div class="workspace-intro-actions">
          <RouterLink
            class="secondary-button knowledge-bases-link"
            data-test="knowledge-bases-link"
            :to="{ name: 'knowledge-bases' }"
          >
            管理知识库
          </RouterLink>
          <button
            class="primary-button create-conversation-button"
            data-test="create-conversation"
            type="button"
            :disabled="conversationStore.isCreating"
            @click="createNewConversation"
          >
            {{ conversationStore.isCreating ? "正在创建…" : "新建会话" }}
          </button>
        </div>
      </section>

      <section class="conversation-panel" aria-labelledby="conversation-list-title">
        <div class="conversation-panel-heading">
          <div>
            <p class="section-label">工作区</p>
            <h2 id="conversation-list-title">我的会话</h2>
          </div>
          <div class="conversation-panel-controls">
            <button
              class="filter-button"
              data-test="toggle-archived"
              type="button"
              :aria-pressed="conversationStore.includeArchived"
              :disabled="conversationStore.isLoading"
              @click="toggleArchivedConversations"
            >
              {{ conversationStore.includeArchived ? "仅看进行中" : "显示已归档" }}
            </button>
            <span v-if="conversationStore.hasLoaded" class="conversation-count">
              {{ conversationStore.conversations.length }} 个会话
            </span>
          </div>
        </div>

        <p
          v-if="conversationStore.isLoading && !conversationStore.hasLoaded"
          class="conversation-status"
          data-test="loading-state"
          role="status"
        >
          正在加载会话…
        </p>

        <div v-if="conversationStore.errorMessage" class="conversation-error" role="alert">
          <p>{{ conversationStore.errorMessage }}</p>
          <button
            class="text-button"
            data-test="retry-load"
            type="button"
            :disabled="conversationStore.isLoading"
            @click="reloadConversations()"
          >
            重新加载
          </button>
        </div>

        <div
          v-if="!conversationStore.isLoading && conversationStore.hasLoaded && conversationStore.conversations.length === 0"
          class="empty-state"
          data-test="empty-state"
        >
          <span class="empty-state-mark" aria-hidden="true">＋</span>
          <h3>还没有会话</h3>
          <p>点击“新建会话”，开始整理一个问题、会议或工作任务。</p>
        </div>

        <ul
          v-else-if="conversationStore.conversations.length > 0"
          class="conversation-list"
          data-test="conversation-list"
        >
          <li v-for="conversation in conversationStore.conversations" :key="conversation.id">
            <article class="conversation-item">
              <RouterLink
                class="conversation-card"
                :to="{ name: 'conversation-detail', params: { conversationId: conversation.id } }"
              >
                <div class="conversation-card-main">
                  <div class="conversation-title-row">
                    <h3>{{ conversation.title }}</h3>
                    <span v-if="conversation.is_archived" class="archived-badge" data-test="archived-badge">
                      已归档
                    </span>
                  </div>
                  <p>{{ conversation.model_name }} · 已使用 {{ conversation.total_tokens }} tokens</p>
                </div>
                <time :datetime="conversation.updated_at">
                  {{ formatUpdatedAt(conversation.updated_at) }}
                </time>
              </RouterLink>
              <div class="conversation-actions" aria-label="会话管理操作">
                <button
                  class="text-button"
                  :data-test="`rename-conversation-${conversation.id}`"
                  type="button"
                  :disabled="conversationStore.isManagingConversation(conversation.id)"
                  @click="openRenameDialog(conversation)"
                >
                  {{ conversationStore.isUpdatingConversation(conversation.id) ? "处理中…" : "重命名" }}
                </button>
                <button
                  v-if="!conversation.is_archived"
                  class="text-button"
                  :data-test="`archive-conversation-${conversation.id}`"
                  type="button"
                  :disabled="conversationStore.isManagingConversation(conversation.id)"
                  @click="archiveConversation(conversation.id)"
                >
                  {{ conversationStore.isUpdatingConversation(conversation.id) ? "处理中…" : "归档" }}
                </button>
                <button
                  class="text-button danger-button"
                  :data-test="`delete-conversation-${conversation.id}`"
                  type="button"
                  :disabled="conversationStore.isManagingConversation(conversation.id)"
                  @click="openDeleteDialog(conversation)"
                >
                  {{ conversationStore.isDeletingConversation(conversation.id) ? "删除中…" : "删除" }}
                </button>
              </div>
              <p
                v-if="conversationStore.actionErrorMessages[conversation.id] && renameConversationId !== conversation.id && deleteTargetConversation?.id !== conversation.id"
                class="conversation-action-error"
                role="alert"
              >
                {{ conversationStore.actionErrorMessages[conversation.id] }}
              </p>
            </article>
          </li>
        </ul>
      </section>
    </main>

    <div
      v-if="renameConversationId"
      class="dialog-backdrop"
      data-test="rename-dialog"
      @click.self="closeRenameDialog"
    >
      <section class="management-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-dialog-title">
        <form @submit.prevent="submitRename">
          <p class="section-label">会话管理</p>
          <h2 id="rename-dialog-title">重命名会话</h2>
          <label class="dialog-field" for="rename-title-input">
            会话标题
            <input
              id="rename-title-input"
              ref="renameInput"
              v-model="renameTitle"
              data-test="rename-input"
              type="text"
              maxlength="200"
              autocomplete="off"
              :disabled="isRenaming"
            >
          </label>
          <p v-if="renameErrorMessage" class="dialog-error" role="alert">
            {{ renameErrorMessage }}
          </p>
          <div class="dialog-actions">
            <button class="secondary-button" type="button" :disabled="isRenaming" @click="closeRenameDialog">
              取消
            </button>
            <button
              class="primary-button"
              data-test="confirm-rename"
              type="submit"
              :disabled="isRenaming || !renameTitle.trim()"
            >
              {{ isRenaming ? "正在保存…" : "保存名称" }}
            </button>
          </div>
        </form>
      </section>
    </div>

    <div
      v-if="deleteTargetConversation"
      class="dialog-backdrop"
      data-test="delete-dialog"
      @click.self="closeDeleteDialog"
    >
      <section class="management-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
        <p class="section-label">不可恢复操作</p>
        <h2 id="delete-dialog-title">删除会话</h2>
        <p class="dialog-description">
          确定删除“{{ deleteTargetConversation.title }}”吗？会话中的所有消息也将无法恢复。
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
            data-test="confirm-delete"
            type="button"
            :disabled="isDeleting"
            @click="confirmDelete"
          >
            {{ isDeleting ? "正在删除…" : "确认删除" }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
