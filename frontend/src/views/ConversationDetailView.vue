<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type { MessageResponse, MessageRole, MessageStatus } from "@/api/contracts";
import ConversationKnowledgeBaseSelector from "@/components/ConversationKnowledgeBaseSelector.vue";
import MessageCitationList from "@/components/MessageCitationList.vue";
import MessageMarkdownContent from "@/components/MessageMarkdownContent.vue";
import { useAuthStore } from "@/stores/auth";
import { useConversationDetailStore } from "@/stores/conversation-detail";
import { resetUserDataStores } from "@/stores/user-data";

const authStore = useAuthStore();
const detailStore = useConversationDetailStore();
const route = useRoute();
const router = useRouter();
const draftContent = ref("");

/** 把机器角色转换为对用户友好的中文标签。 */
function roleLabel(role: MessageRole): string {
  const labels: Record<MessageRole, string> = {
    system: "系统",
    user: "你",
    assistant: "AI 助手",
    tool: "工具",
  };
  return labels[role];
}

/** 只为非完成消息显示生命周期状态，正常历史消息不增加视觉噪音。 */
function statusLabel(status: MessageStatus): string | null {
  const labels: Partial<Record<MessageStatus, string>> = {
    pending: "生成中",
    failed: "生成失败",
    cancelled: "已取消",
  };
  return labels[status] ?? null;
}

/** 仅失败或取消的助手消息允许重试；重试中仍保留按钮以展示当前状态。 */
function isRetryableMessage(message: MessageResponse): boolean {
  return (
    message.role === "assistant" &&
    (message.status === "failed" || message.status === "cancelled")
  );
}

/** 用本地可读时间展示后端 ISO 值，便于查看历史消息顺序。 */
function formatMessageTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 当前路由 ID 改变时读取新会话；Store 会忽略较晚返回的旧请求。 */
function loadRouteConversation(conversationId: unknown): void {
  if (typeof conversationId === "string" && conversationId.length > 0) {
    void detailStore.load(conversationId);
  }
}

/** 提交本地草稿；开始生成后立即清空输入框，消息正文由 started 事件回填。 */
async function submitMessage(): Promise<void> {
  const content = draftContent.value;
  if (!content.trim()) {
    await detailStore.sendMessage(content);
    return;
  }
  draftContent.value = "";
  await detailStore.sendMessage(content);
}

/** 退出时清除全部用户私有状态，避免切换账号后留下会话或知识库数据。 */
async function logout(): Promise<void> {
  resetUserDataStores();
  authStore.logout();
  await router.replace({ name: "login" });
}

watch(
  () => route.params.conversationId,
  loadRouteConversation,
  { immediate: true },
);
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

    <main class="workspace-main detail-main">
      <p v-if="authStore.sessionError" class="session-warning" role="status">
        {{ authStore.sessionError }}
      </p>
      <RouterLink class="back-link" data-test="back-to-list" :to="{ name: 'home' }">
        ← 返回会话列表
      </RouterLink>

      <p
        v-if="detailStore.isLoading && !detailStore.hasLoaded"
        class="conversation-status detail-loading"
        data-test="detail-loading-state"
        role="status"
      >
        正在加载会话…
      </p>

      <section
        v-else-if="detailStore.errorMessage && !detailStore.hasLoaded"
        class="detail-error-state"
        role="alert"
      >
        <p class="section-label">无法打开会话</p>
        <h1>会话详情暂不可用</h1>
        <p>{{ detailStore.errorMessage }}</p>
      </section>

      <template v-else-if="detailStore.conversation">
        <section class="detail-header" aria-labelledby="conversation-title">
          <p class="eyebrow">Conversation History</p>
          <div class="detail-title-row">
            <div>
              <h1 id="conversation-title">{{ detailStore.conversation.title }}</h1>
              <p>
                {{ detailStore.conversation.model_name }} · 温度 {{ detailStore.conversation.temperature }}
                · 已使用 {{ detailStore.conversation.total_tokens }} tokens
              </p>
            </div>
            <span v-if="detailStore.conversation.is_archived" class="conversation-count">已归档</span>
          </div>
          <p v-if="detailStore.isLoading" class="refresh-status" role="status">正在刷新历史消息…</p>
          <div v-if="detailStore.errorMessage" class="conversation-error" role="alert">
            <p>{{ detailStore.errorMessage }}</p>
            <button class="text-button" type="button" @click="loadRouteConversation(route.params.conversationId)">
              重新加载
            </button>
          </div>
        </section>

        <ConversationKnowledgeBaseSelector
          :conversation-id="detailStore.conversation.id"
          :disabled="detailStore.isSending || detailStore.conversation.is_archived"
        />

        <section class="message-history" aria-labelledby="message-history-title">
          <div class="conversation-panel-heading">
            <div>
              <p class="section-label">已保存历史</p>
              <h2 id="message-history-title">消息记录</h2>
            </div>
            <span v-if="detailStore.hasLoaded" class="conversation-count">
              {{ detailStore.messages.length }} 条消息
            </span>
          </div>

          <div v-if="detailStore.hasLoaded && detailStore.messages.length === 0" class="empty-state" data-test="message-empty-state">
            <span class="empty-state-mark" aria-hidden="true">◌</span>
            <h3>还没有消息</h3>
            <p>输入你的问题，开始一次流式对话。</p>
          </div>

          <ol v-else class="message-list" data-test="message-list">
            <li v-for="message in detailStore.messages" :key="message.id" :class="['message-item', `message-item--${message.role}`]">
              <article class="message-card">
                <header class="message-card-header">
                  <span>{{ roleLabel(message.role) }}</span>
                  <div>
                    <span v-if="statusLabel(message.status)" class="message-status">
                      {{ statusLabel(message.status) }}
                    </span>
                    <time :datetime="message.created_at">{{ formatMessageTime(message.created_at) }}</time>
                  </div>
                </header>
                <MessageMarkdownContent
                  v-if="message.role === 'assistant'"
                  :content="message.content"
                />
                <p v-else class="message-content">
                  {{ message.content || "消息正文为空" }}
                </p>
                <MessageCitationList
                  v-if="message.role === 'assistant' && message.citations?.length"
                  :sources="message.citations"
                />
                <p v-if="message.error_message" class="message-error">{{ message.error_message }}</p>
                <p v-if="detailStore.retryErrorMessages[message.id]" class="message-error">
                  {{ detailStore.retryErrorMessages[message.id] }}
                </p>
                <button
                  v-if="isRetryableMessage(message) || detailStore.isRetryingMessage(message.id)"
                  class="message-retry-button"
                  :data-test="`retry-message-${message.id}`"
                  type="button"
                  :disabled="detailStore.isRetryingMessage(message.id) || detailStore.isSending"
                  @click="detailStore.retryMessage(message.id)"
                >
                  {{ detailStore.isRetryingMessage(message.id) ? "正在重试…" : "重试" }}
                </button>
              </article>
            </li>
          </ol>
        </section>

        <section class="message-composer" aria-labelledby="message-composer-title">
          <div class="composer-heading">
            <div>
              <p class="section-label">继续对话</p>
              <h2 id="message-composer-title">发送消息</h2>
            </div>
            <span v-if="detailStore.isSending" class="composer-streaming" role="status">
              {{ detailStore.isCancelling ? "正在停止生成…" : "AI 正在生成…" }}
            </span>
          </div>

          <form class="message-form" data-test="message-form" @submit.prevent="submitMessage">
            <label for="message-input">你的问题</label>
            <textarea
              id="message-input"
              v-model="draftContent"
              data-test="message-input"
              rows="4"
              placeholder="例如：请整理这次项目会议的目标、风险和待办事项"
              :disabled="detailStore.isSending"
            />
            <p v-if="detailStore.sendErrorMessage" class="composer-error" role="alert">
              {{ detailStore.sendErrorMessage }}
            </p>
            <div class="composer-actions">
              <p>Enter 可换行；发送后会逐段显示模型回复。</p>
              <div>
                <button
                  v-if="detailStore.isSending"
                  class="secondary-button"
                  data-test="cancel-stream"
                  type="button"
                  :disabled="detailStore.isCancelling"
                  @click="detailStore.cancelActiveStream"
                >
                  {{ detailStore.isCancelling ? "正在停止…" : "停止生成" }}
                </button>
                <button
                  class="primary-button send-message-button"
                  data-test="send-message"
                  type="submit"
                  :disabled="detailStore.isSending || !detailStore.hasLoaded"
                >
                  {{ detailStore.isSending ? "正在生成…" : "发送消息" }}
                </button>
              </div>
            </div>
          </form>
        </section>
      </template>
    </main>
  </div>
</template>
