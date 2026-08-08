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
const confirmPassword = ref("");
const errorMessage = ref<string | null>(null);

/** 在提交前提供注册表单的即时校验；后端仍负责最终邮箱和密码校验。 */
function validateRegistration(): string | null {
  const normalizedEmail = email.value.trim();
  if (!/^\S+@\S+\.\S+$/.test(normalizedEmail)) {
    return "请输入有效的邮箱地址";
  }
  if (password.value.length < 8) {
    return "密码至少需要 8 位";
  }
  if (password.value !== confirmPassword.value) {
    return "两次输入的密码不一致";
  }
  return null;
}

/** 注册成功后自动登录，并沿用与登录页相同的安全跳转规则。 */
async function submitRegistration(): Promise<void> {
  if (authStore.loading) {
    return;
  }
  errorMessage.value = validateRegistration();
  if (errorMessage.value) {
    return;
  }

  try {
    await authStore.registerAndLogin(
      email.value.trim().toLowerCase(),
      password.value,
    );
    await router.replace(resolveSafeRedirect(route.query.redirect));
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : "注册失败，请稍后重试";
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-story" aria-labelledby="product-title">
      <div class="brand-mark" aria-hidden="true">EA</div>
      <p class="eyebrow">Enterprise AI Assistant</p>
      <h1 id="product-title">从一个账号开始，建立可追溯的 AI 工作流</h1>
      <p class="story-copy">
        注册后可体验多轮会话、企业知识检索和经过治理的办公行动。
      </p>
      <ul class="capability-list" aria-label="系统能力">
        <li>可追溯知识检索</li>
        <li>MCP 工具治理</li>
        <li>人工审批与审计</li>
      </ul>
    </section>

    <section class="auth-panel" aria-labelledby="register-title">
      <div class="panel-heading">
        <p class="section-label">开始使用</p>
        <h2 id="register-title">创建账号</h2>
        <p>注册成功后将自动登录并进入工作台。</p>
      </div>

      <form class="login-form" novalidate @submit.prevent="submitRegistration">
        <label for="register-email">邮箱</label>
        <input
          id="register-email"
          v-model="email"
          data-test="register-email"
          type="email"
          autocomplete="email"
          placeholder="name@company.com"
          :disabled="authStore.loading"
        >

        <div class="password-label">
          <label for="register-password">密码</label>
          <span>至少 8 位</span>
        </div>
        <input
          id="register-password"
          v-model="password"
          data-test="register-password"
          type="password"
          autocomplete="new-password"
          placeholder="设置登录密码"
          :disabled="authStore.loading"
        >

        <label for="register-confirm-password">确认密码</label>
        <input
          id="register-confirm-password"
          v-model="confirmPassword"
          data-test="register-confirm-password"
          type="password"
          autocomplete="new-password"
          placeholder="再次输入登录密码"
          :disabled="authStore.loading"
        >

        <p v-if="errorMessage" class="form-error" role="alert">
          {{ errorMessage }}
        </p>

        <button class="primary-button" type="submit" :disabled="authStore.loading">
          {{ authStore.loading ? "正在创建…" : "创建账号" }}
        </button>
      </form>

      <p class="auth-switch">
        已有账号？
        <RouterLink
          data-test="go-login"
          :to="{
            name: 'login',
            query: route.query.redirect ? { redirect: route.query.redirect } : {},
          }"
        >
          去登录
        </RouterLink>
      </p>
      <p class="security-note">令牌只保存在当前浏览器标签页，不保存密码。</p>
    </section>
  </main>
</template>
