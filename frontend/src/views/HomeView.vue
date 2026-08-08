<script setup lang="ts">
import { onMounted } from "vue";
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";

const DEFAULT_CONVERSATION_TITLE = "新建会话";

const authStore = useAuthStore();
const conversationStore = useConversationStore();
const router = useRouter();

/** 只在第一次进入工作台时读取列表，返回此页时复用已加载的 Store 状态。 */
async function reloadConversations(): Promise<void> {
  await conversationStore.loadConversations();
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

/** 使用第一版默认标题创建会话；重命名会在后续会话管理模块完成。 */
async function createNewConversation(): Promise<void> {
  await conversationStore.createConversation({
    title: DEFAULT_CONVERSATION_TITLE,
  });
}

/** 清理所有当前用户内存状态并替换历史记录，避免后退回受保护页面。 */
async function logout(): Promise<void> {
  conversationStore.reset();
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
        <button
          class="primary-button create-conversation-button"
          data-test="create-conversation"
          type="button"
          :disabled="conversationStore.isCreating"
          @click="createNewConversation"
        >
          {{ conversationStore.isCreating ? "正在创建…" : "新建会话" }}
        </button>
      </section>

      <section class="conversation-panel" aria-labelledby="conversation-list-title">
        <div class="conversation-panel-heading">
          <div>
            <p class="section-label">工作区</p>
            <h2 id="conversation-list-title">我的会话</h2>
          </div>
          <span v-if="conversationStore.hasLoaded" class="conversation-count">
            {{ conversationStore.conversations.length }} 个会话
          </span>
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
            @click="reloadConversations"
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
            <RouterLink
              class="conversation-card"
              :to="{ name: 'conversation-detail', params: { conversationId: conversation.id } }"
            >
              <div class="conversation-card-main">
                <h3>{{ conversation.title }}</h3>
                <p>{{ conversation.model_name }} · 已使用 {{ conversation.total_tokens }} tokens</p>
              </div>
              <time :datetime="conversation.updated_at">
                {{ formatUpdatedAt(conversation.updated_at) }}
              </time>
            </RouterLink>
          </li>
        </ul>
      </section>
    </main>
  </div>
</template>
