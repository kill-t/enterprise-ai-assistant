import { defineStore } from "pinia";

import {
  getCurrentUser,
  login as loginRequest,
  register as registerRequest,
} from "@/api/auth";
import { ApiError } from "@/api/client";
import type { UserResponse } from "@/api/contracts";

const ACCESS_TOKEN_KEY = "enterprise-ai-assistant.access-token";

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  loading: boolean;
  sessionInitialized: boolean;
  sessionError: string | null;
}

/** 管理当前标签页的短期 JWT 与用户公开信息。 */
export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({
    // sessionStorage 支持刷新恢复，但关闭标签页后清除；它仍不能防御 XSS。
    token: sessionStorage.getItem(ACCESS_TOKEN_KEY),
    user: null,
    loading: false,
    sessionInitialized: false,
    sessionError: null,
  }),
  getters: {
    isAuthenticated: (state): boolean => Boolean(state.token),
  },
  actions: {
    /** 登录成功后只保存短期 Access Token，不持久化密码。 */
    async login(email: string, password: string): Promise<void> {
      this.loading = true;
      try {
        const response = await loginRequest(email, password);
        this.token = response.access_token;
        this.user = null;
        this.sessionInitialized = false;
        this.sessionError = null;
        sessionStorage.setItem(ACCESS_TOKEN_KEY, response.access_token);
      } finally {
        this.loading = false;
      }
    },

    /** 注册成功后立即走既有登录接口，避免前端自行伪造 Token 或用户状态。 */
    async registerAndLogin(email: string, password: string): Promise<void> {
      this.loading = true;
      try {
        await registerRequest({ email, password });
        const response = await loginRequest(email, password);
        this.token = response.access_token;
        this.user = null;
        this.sessionInitialized = false;
        this.sessionError = null;
        sessionStorage.setItem(ACCESS_TOKEN_KEY, response.access_token);
      } finally {
        this.loading = false;
      }
    },

    /** 刷新页面后读取 /me；令牌失效时清理会话，临时网络错误继续上抛。 */
    async loadCurrentUser(): Promise<UserResponse | null> {
      if (!this.token) {
        return null;
      }
      this.loading = true;
      try {
        this.user = await getCurrentUser(this.token);
        return this.user;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.logout();
        }
        throw error;
      } finally {
        this.loading = false;
      }
    },

    /** 首次路由导航时只验证一次已有 Token，并区分失效与临时断网。 */
    async ensureSession(): Promise<void> {
      if (this.sessionInitialized) {
        return;
      }
      if (!this.token) {
        this.sessionInitialized = true;
        this.sessionError = null;
        return;
      }

      try {
        await this.loadCurrentUser();
        this.sessionError = null;
      } catch (error) {
        // loadCurrentUser 已在 401 时 logout；其他故障保留 Token，避免断网强退。
        if (this.token) {
          this.sessionError =
            error instanceof ApiError
              ? error.message
              : "暂时无法验证登录状态，请稍后重试";
        }
      } finally {
        this.sessionInitialized = true;
      }
    },

    /** 清除 Pinia 与当前标签页存储的全部认证状态。 */
    logout(): void {
      this.token = null;
      this.user = null;
      this.sessionInitialized = true;
      this.sessionError = null;
      sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    },
  },
});
