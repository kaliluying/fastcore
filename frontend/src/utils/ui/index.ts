/** UI 辅助工具（集中定义）。 */

import NProgress from "nprogress";
import { ElLoading } from "element-plus";
import { setElementThemeColor as applyElementThemeColor } from "./colors";
import { useSettingsStore } from "@/store/modules/setting.store";
import { fourDotsSpinnerSvg } from "@/assets/svg/loading";
import { useTheme } from "@/hooks/core/useTheme";
import { SystemThemeEnum } from "@/enums/appEnum";

// -----------------------------
// NProgress 进度条
// -----------------------------

NProgress.configure({
  easing: "ease",
  speed: 500,
  showSpinner: false,
  trickleSpeed: 200,
  minimum: 0.3,
});

export { NProgress };

export * from "./colors";

/** 保留页面调用方式；底层颜色工具不依赖 Store。 */
export function setElementThemeColor(color: string): void {
  applyElementThemeColor(color, useSettingsStore().isDark);
}

// -----------------------------
// 加载状态
// -----------------------------

const getLoadingBackground = (): string => {
  const isDark = document.documentElement.classList.contains("dark");
  return isDark ? "rgba(7, 7, 7, 0.85)" : "#fff";
};

const DEFAULT_LOADING_CONFIG = {
  lock: true,
  get background() {
    return getLoadingBackground();
  },
  svg: fourDotsSpinnerSvg,
  svgViewBox: "0 0 40 40",
  customClass: "fa-loading-fix",
} as const;

interface LoadingInstance {
  close: () => void;
}

let loadingInstance: LoadingInstance | null = null;

export const loadingService = {
  showLoading(): () => void {
    if (!loadingInstance) {
      const config = { ...DEFAULT_LOADING_CONFIG, background: getLoadingBackground() };
      loadingInstance = ElLoading.service(config);
    }
    return () => loadingService.hideLoading();
  },

  hideLoading(): void {
    if (!loadingInstance) return;
    loadingInstance.close();
    loadingInstance = null;
  },
};

// -----------------------------
// 主题动画
// -----------------------------

const { LIGHT, DARK } = SystemThemeEnum;

export const themeAnimation = (e: MouseEvent) => {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    toggleTheme();
    return;
  }
  const x = e.clientX;
  const y = e.clientY;
  const endRadius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));

  // 动态注入含硬编码坐标的 @keyframes，绕过 View Transition 伪元素对 CSS 变量的继承问题
  const id = "vt-clip-kf";
  let el = document.getElementById(id) as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = id;
    document.head.appendChild(el);
  }
  el.textContent = `@keyframes clip{from{clip-path:circle(0% at ${x}px ${y}px)}to{clip-path:circle(${endRadius}px at ${x}px ${y}px)}}`;
  document.head.appendChild(el);

  requestAnimationFrame(() => {
    if (document.startViewTransition) document.startViewTransition(() => toggleTheme());
    else toggleTheme();
  });
};

const toggleTheme = () => {
  // 主题切换通过 CSS 变量 + html.dark 类名实时应用，无需重建 RouterView。
  // 历史代码曾在这里调用 useCommon().refresh() 触发整页重建，会造成闪烁，
  // 且对正确实现的主题切换没有必要 —— 移除后切换即无刷新。
  useTheme().switchThemeStyles(useSettingsStore().systemThemeType === LIGHT ? DARK : LIGHT);
};

export const toggleTransition = (enable: boolean) => {
  const body = document.body;

  if (enable) body.classList.add("theme-change");
  else {
    setTimeout(() => {
      body.classList.remove("theme-change");
    }, 300);
  }
};
