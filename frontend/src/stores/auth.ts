import { defineStore } from "pinia";

import { getCurrentUser, login as loginRequest } from "@/api/auth";
import { ApiError } from "@/api/client";
import type { UserResponse } from "@/api/contracts";

const ACCESS_TOKEN_KEY = "enterprise-ai-assistant.access-token";

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  loading: boolean;
}

/** 管理当前标签页的短期 JWT 与用户公开信息。 */
export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({
    // sessionStorage 支持刷新恢复，但关闭标签页后清除；它仍不能防御 XSS。
    token: sessionStorage.getItem(ACCESS_TOKEN_KEY),
    user: null,
    loading: false,
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

    /** 清除 Pinia 与当前标签页存储的全部认证状态。 */
    logout(): void {
      this.token = null;
      this.user = null;
      sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    },
  },
});
