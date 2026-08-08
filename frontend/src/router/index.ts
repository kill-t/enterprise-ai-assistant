import type { Pinia } from "pinia";
import {
  createRouter,
  createWebHistory,
  type Router,
  type RouterHistory,
} from "vue-router";

import { useAuthStore } from "@/stores/auth";
import ConversationDetailView from "@/views/ConversationDetailView.vue";
import HomeView from "@/views/HomeView.vue";
import LoginView from "@/views/LoginView.vue";

declare module "vue-router" {
  interface RouteMeta {
    requiresAuth?: boolean;
    guestOnly?: boolean;
  }
}

/** 创建应用路由；测试可传入 memory history，生产默认使用浏览器 history。 */
export function createAppRouter(
  pinia: Pinia,
  history: RouterHistory = createWebHistory(import.meta.env.BASE_URL),
): Router {
  const router = createRouter({
    history,
    routes: [
      {
        path: "/login",
        name: "login",
        component: LoginView,
        meta: { guestOnly: true },
      },
      {
        path: "/",
        name: "home",
        component: HomeView,
        meta: { requiresAuth: true },
      },
      {
        path: "/conversations/:conversationId",
        name: "conversation-detail",
        component: ConversationDetailView,
        meta: { requiresAuth: true },
      },
      {
        path: "/:pathMatch(.*)*",
        redirect: "/",
      },
    ],
  });

  router.beforeEach(async (to) => {
    const authStore = useAuthStore(pinia);
    await authStore.ensureSession();

    if (to.meta.requiresAuth && !authStore.isAuthenticated) {
      return {
        name: "login",
        query: { redirect: to.fullPath },
      };
    }
    if (to.meta.guestOnly && authStore.isAuthenticated) {
      return { name: "home" };
    }
    return true;
  });

  return router;
}
