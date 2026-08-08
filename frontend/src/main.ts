import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "@/App.vue";
import "@/style.css";

// Pinia 在根应用统一注册，后续页面和组合式函数共享同一份身份状态。
createApp(App).use(createPinia()).mount("#app");
