<script setup lang="ts">
import { watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type { MessageRole, MessageStatus } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useConversationDetailStore } from "@/stores/conversation-detail";
import { useConversationStore } from "@/stores/conversations";

const authStore = useAuthStore();
const conversationStore = useConversationStore();
const detailStore = useConversationDetailStore();
const route = useRoute();
const router = useRouter();

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

/** 退出时同时清除列表与详情，避免切换账号后留下任何会话内存数据。 */
async function logout(): Promise<void> {
  detailStore.reset();
  conversationStore.reset();
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
            <p>下一模块会在这里加入流式对话输入框。</p>
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
                <p class="message-content">{{ message.content || "模型未产生正文" }}</p>
                <p v-if="message.error_message" class="message-error">{{ message.error_message }}</p>
              </article>
            </li>
          </ol>
        </section>
      </template>
    </main>
  </div>
</template>
