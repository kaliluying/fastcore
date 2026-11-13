// ---------------------------------------------------------------------------
// 样式（顺序：tailwind 基础 → 项目全局 → Element Plus 暗色 → 动画库）
// ---------------------------------------------------------------------------
import "@styles/tailwind.css";
import "@styles/index.scss";

// ---------------------------------------------------------------------------
// 应用初始化
// ---------------------------------------------------------------------------
import App from "./App.vue";
import { createApp } from "vue";
import { printConsoleBanner, startVersionCheck } from "@utils";
import { initPlugins } from "@/plugins";

/**
 * iOS Safari 中支持 `:active` CSS 伪类响应，同时显式声明 `{ passive: true }` 避免滚动性能损耗。
 */
document.addEventListener("touchstart", function () {}, { passive: true });

/** 启动顺序：
 *  1. printConsoleBanner  —— 控制台欢迎信息（无依赖）
 *  2. initPlugins         —— 注册所有 Vue 插件（Pinia → Router → 指令 → 国际化 → Element Plus）
 *  3. mount               —— 挂载根组件
 *  挂载后 App.vue 的 onMounted 中初始化站点标题/favicon（需等 Pinia store 就绪）
 */
(async () => {
  const app = createApp(App);
  printConsoleBanner();
  await initPlugins(app);
  app.mount("#app");
  startVersionCheck();
})();
