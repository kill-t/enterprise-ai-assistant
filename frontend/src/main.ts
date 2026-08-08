import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "@/App.vue";
import { createAppRouter } from "@/router";
import "@/style.css";

// 路由工厂显式接收 Pinia，导航守卫与组件共享同一份身份状态。
const pinia = createPinia();
const router = createAppRouter(pinia);
createApp(App).use(pinia).use(router).mount("#app");
