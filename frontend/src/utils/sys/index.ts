/** 系统辅助工具（集中定义）。 */

import type { App } from "vue";
import mitt, { type Emitter } from "mitt";
import { StorageConfig } from "../storage";

/** 输出当前实例的简要启动信息。 */
export function printConsoleBanner(): void {
  console.info(`[${StorageConfig.appName}] v${StorageConfig.CURRENT_VERSION} 已启动`);
}

// -----------------------------
// Mitt 事件总线
// -----------------------------

type SysEvents = {
  openSearchDialog: void;
};

export const mittBus: Emitter<SysEvents> = mitt<SysEvents>();

// -----------------------------
// 错误处理
// -----------------------------

const IGNORABLE_SCRIPT_ERRORS = [
  "ResizeObserver loop completed with undelivered notifications.",
  "ResizeObserver loop limit exceeded",
];

function normalizeErrorMessage(message: Event | string): string {
  if (typeof message === "string") return message;
  if ("message" in message && typeof message.message === "string") return message.message;
  return "";
}

function isIgnorableScriptError(message: Event | string, source?: string): boolean {
  const normalizedMessage = normalizeErrorMessage(message);
  if (!normalizedMessage) return false;

  if (IGNORABLE_SCRIPT_ERRORS.some((item) => normalizedMessage.includes(item))) {
    // 浏览器/扩展在布局抖动时常见的 ResizeObserver 噪声，不作为真实异常处理
    return true;
  }

  // 浏览器扩展注入脚本偶发的跨域 Script error 也没有排查价值
  if (normalizedMessage === "Script error." && source === "") return true;
  return false;
}

export function vueErrorHandler(err: unknown, instance: any, info: string) {
  console.error("[VueError]", err, info, instance);
}

export function scriptErrorHandler(
  message: Event | string,
  source?: string,
  lineno?: number,
  colno?: number,
  error?: Error
): boolean {
  if (isIgnorableScriptError(message, source)) return true;
  console.error("[ScriptError]", { message, source, lineno, colno, error });
  return true;
}

export function registerPromiseErrorHandler() {
  window.addEventListener("unhandledrejection", (event) => {
    console.error("[PromiseError]", event.reason);
  });
}

export function registerResourceErrorHandler() {
  window.addEventListener(
    "error",
    (event: Event) => {
      const target = event.target as HTMLElement;
      if (
        target &&
        (target.tagName === "IMG" || target.tagName === "SCRIPT" || target.tagName === "LINK")
      ) {
        console.error("[ResourceError]", {
          tagName: target.tagName,
          src:
            (target as HTMLImageElement).src ||
            (target as HTMLScriptElement).src ||
            (target as HTMLLinkElement).href,
        });
      }
    },
    true
  );
}

export function initErrorHandle(app: App) {
  app.config.errorHandler = vueErrorHandler;
  window.onerror = scriptErrorHandler;
  registerPromiseErrorHandler();
  registerResourceErrorHandler();
}
