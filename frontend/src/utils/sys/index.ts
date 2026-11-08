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
  triggerFireworks: string | undefined;
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

// -----------------------------
// 版本升级
// -----------------------------

/**
 * 版本升级管理器。
 *
 * ── 检测逻辑 ──
 * 1. 跳过 1.0.0 版本（无需升级的基版本）
 * 2. 首次访问 → 写入当前版本号，不升级
 * 3. 版本相同 → 无需升级
 * 4. 版本不同 + 存在旧数据 → 清理旧 key
 * 5. 版本不同 + 无旧数据 → 仅更新版本号
 */
class VersionManager {
  private getStoredVersion(): string | null {
    return localStorage.getItem(StorageConfig.VERSION_KEY);
  }

  private setStoredVersion(version: string): void {
    localStorage.setItem(StorageConfig.VERSION_KEY, version);
  }

  private shouldSkipUpgrade(): boolean {
    return StorageConfig.CURRENT_VERSION === StorageConfig.SKIP_UPGRADE_VERSION;
  }

  private isFirstVisit(storedVersion: string | null): boolean {
    return !storedVersion;
  }

  private isSameVersion(storedVersion: string): boolean {
    return storedVersion === StorageConfig.CURRENT_VERSION;
  }

  private findLegacyStorage(): string[] {
    const storageKeys = Object.keys(localStorage);
    return storageKeys.filter(
      (key) => StorageConfig.isVersionedKey(key) && !StorageConfig.isCurrentVersionKey(key)
    );
  }

  private cleanupLegacyData(oldVersionKeys: string[]): void {
    oldVersionKeys.forEach((key) => {
      localStorage.removeItem(key);
      console.info(`[Upgrade] 已清理旧存储: ${key}`);
    });
  }

  private executeUpgrade(storedVersion: string, oldVersionKeys: string[]): void {
    this.cleanupLegacyData(oldVersionKeys);
    this.setStoredVersion(StorageConfig.CURRENT_VERSION);
    console.info(`[Storage] 已清理旧版本数据: ${storedVersion} → ${StorageConfig.CURRENT_VERSION}`);
  }

  async processUpgrade(): Promise<void> {
    if (this.shouldSkipUpgrade()) {
      console.debug("[Upgrade] 跳过版本升级检查");
      return;
    }

    const storedVersion = this.getStoredVersion();
    if (this.isFirstVisit(storedVersion)) {
      this.setStoredVersion(StorageConfig.CURRENT_VERSION);
      console.info("[Upgrade] 首次访问，已设置当前版本");
      return;
    }

    if (this.isSameVersion(storedVersion!)) {
      console.debug("[Upgrade] 版本相同，无需升级");
      return;
    }

    const oldVersionKeys = this.findLegacyStorage();
    if (oldVersionKeys.length === 0) {
      this.setStoredVersion(StorageConfig.CURRENT_VERSION);
      console.info("[Upgrade] 无旧数据，已更新版本号");
      return;
    }

    this.executeUpgrade(storedVersion!, oldVersionKeys);
  }
}

export async function processUpgrade(): Promise<void> {
  const versionManager = new VersionManager();
  await versionManager.processUpgrade();
}

export function systemUpgrade(): void {
  setTimeout(() => {
    void processUpgrade();
  }, StorageConfig.UPGRADE_DELAY);
}
