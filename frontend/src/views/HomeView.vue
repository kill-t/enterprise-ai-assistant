<script setup lang="ts">
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const authStore = useAuthStore();
const router = useRouter();

/** 清理本地身份状态并替换历史记录，避免后退回受保护页面。 */
async function logout(): Promise<void> {
  authStore.logout();
  await router.replace({ name: "login" });
}
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
      <section class="workspace-hero" aria-labelledby="workspace-title">
        <p class="eyebrow">Authenticated Workspace</p>
        <h1 id="workspace-title">身份验证已接通</h1>
        <p>
          当前页面受到前端路由保护，FastAPI 仍会对每个数据请求执行 JWT 与所有权校验。下一模块将在这里加入会话列表。
        </p>
        <div class="status-grid">
          <article>
            <span>01</span>
            <strong>短期 JWT</strong>
            <p>刷新页面可恢复，关闭标签页自动清除。</p>
          </article>
          <article>
            <span>02</span>
            <strong>路由守卫</strong>
            <p>未登录访问会保留原地址并跳转登录。</p>
          </article>
          <article>
            <span>03</span>
            <strong>后端授权</strong>
            <p>真正权限仍由 FastAPI 和 PostgreSQL 过滤保证。</p>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>
