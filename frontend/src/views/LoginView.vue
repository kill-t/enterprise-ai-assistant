<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError } from "@/api/client";
import { resolveSafeRedirect } from "@/router/redirect";
import { useAuthStore } from "@/stores/auth";

const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();

const email = ref("");
const password = ref("");
const errorMessage = ref<string | null>(null);

/** 在请求前提供即时校验，后端仍会执行最终身份校验。 */
function validateCredentials(): string | null {
  const normalizedEmail = email.value.trim();
  if (!/^\S+@\S+\.\S+$/.test(normalizedEmail)) {
    return "请输入有效的邮箱地址";
  }
  if (password.value.length < 8) {
    return "密码至少需要 8 位";
  }
  return null;
}

/** 提交登录并跳回原受保护地址，所有错误都转换为页面可读中文提示。 */
async function submitLogin(): Promise<void> {
  if (authStore.loading) {
    return;
  }
  errorMessage.value = validateCredentials();
  if (errorMessage.value) {
    return;
  }

  try {
    await authStore.login(email.value.trim().toLowerCase(), password.value);
    await router.replace(resolveSafeRedirect(route.query.redirect));
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : "登录失败，请稍后重试";
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-story" aria-labelledby="product-title">
      <div class="brand-mark" aria-hidden="true">EA</div>
      <p class="eyebrow">Enterprise AI Assistant</p>
      <h1 id="product-title">把企业知识与办公行动连接起来</h1>
      <p class="story-copy">
        从资料检索到会议准备，再到经过审批的邮件和任务执行。先登录，进入可追溯、可恢复的 AI 工作流。
      </p>
      <ul class="capability-list" aria-label="系统能力">
        <li>可追溯知识检索</li>
        <li>MCP 工具治理</li>
        <li>人工审批与审计</li>
      </ul>
    </section>

    <section class="auth-panel" aria-labelledby="login-title">
      <div class="panel-heading">
        <p class="section-label">欢迎回来</p>
        <h2 id="login-title">登录工作台</h2>
        <p>使用已注册的企业邮箱继续。</p>
      </div>

      <form class="login-form" novalidate @submit.prevent="submitLogin">
        <label for="email">邮箱</label>
        <input
          id="email"
          v-model="email"
          data-test="email"
          type="email"
          autocomplete="email"
          placeholder="name@company.com"
          :disabled="authStore.loading"
        />

        <div class="password-label">
          <label for="password">密码</label>
          <span>至少 8 位</span>
        </div>
        <input
          id="password"
          v-model="password"
          data-test="password"
          type="password"
          autocomplete="current-password"
          placeholder="输入登录密码"
          :disabled="authStore.loading"
        />

        <p v-if="errorMessage" class="form-error" role="alert">
          {{ errorMessage }}
        </p>

        <button class="primary-button" type="submit" :disabled="authStore.loading">
          {{ authStore.loading ? "登录中…" : "登录" }}
        </button>
      </form>

      <p class="auth-switch">
        还没有账号？
        <RouterLink
          data-test="go-register"
          :to="{
            name: 'register',
            query: route.query.redirect ? { redirect: route.query.redirect } : {},
          }"
        >
          创建账号
        </RouterLink>
      </p>
      <p class="security-note">令牌只保存在当前浏览器标签页，不保存密码。</p>
    </section>
  </main>
</template>
