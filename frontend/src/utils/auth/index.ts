/**
 * Auth 认证令牌管理。
 *
 * 注意：令牌存储直接操作 localStorage / sessionStorage，而非经过 @utils/storage 的
 * Storage 工具类。这是因为：
 * 1. 令牌使用固定键名（access_token / refresh_token），不需要版本化键名前缀
 * 2. rememberMe 机制需要在两端（localStorage / sessionStorage）间切换
 * 3. 令牌值已是字符串，无需 JSON 序列化/反序列化
 *
 * @module Auth
 */

const AUTH_KEYS = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token",
  REMEMBER_ME: "remember_me",
  SESSION_KEY: "auth_session",
} as const;

const refreshPromises = new Map<string, Promise<void>>();

export class Auth {
  static isLoggedIn(): boolean {
    return !!Auth.getAccessToken();
  }

  static getAccessToken(): string {
    const isRememberMe = Auth.getRememberMe();
    return isRememberMe
      ? localStorage.getItem(AUTH_KEYS.ACCESS_TOKEN) || ""
      : sessionStorage.getItem(AUTH_KEYS.ACCESS_TOKEN) || "";
  }

  static getRefreshToken(): string {
    const isRememberMe = Auth.getRememberMe();
    return isRememberMe
      ? localStorage.getItem(AUTH_KEYS.REFRESH_TOKEN) || ""
      : sessionStorage.getItem(AUTH_KEYS.REFRESH_TOKEN) || "";
  }

  /** 请求共享按登录会话隔离，同一会话的令牌续期不改变标识。 */
  static getSessionKey(): string {
    if (!this.getAccessToken()) return "";
    const storage = this.getRememberMe() ? localStorage : sessionStorage;
    let key = storage.getItem(AUTH_KEYS.SESSION_KEY);
    // 为升级前已登录的会话补建标识，刷新页面后仍使用同一标识。
    if (!key) {
      key = this.createSessionKey();
      storage.setItem(AUTH_KEYS.SESSION_KEY, key);
    }
    return key;
  }

  static setTokens(accessToken: string, refreshToken: string, rememberMe: boolean): void {
    this.storeTokens(accessToken, refreshToken, rememberMe, this.createSessionKey());
  }

  private static createSessionKey(): string {
    return crypto.getRandomValues(new Uint32Array(4)).join("-");
  }

  private static storeTokens(
    accessToken: string,
    refreshToken: string,
    rememberMe: boolean,
    sessionKey: string
  ): void {
    const storage = rememberMe ? localStorage : sessionStorage;
    const otherStorage = rememberMe ? sessionStorage : localStorage;
    storage.setItem(AUTH_KEYS.ACCESS_TOKEN, accessToken);
    storage.setItem(AUTH_KEYS.REFRESH_TOKEN, refreshToken);
    storage.setItem(AUTH_KEYS.SESSION_KEY, sessionKey);
    for (const key of [AUTH_KEYS.ACCESS_TOKEN, AUTH_KEYS.REFRESH_TOKEN, AUTH_KEYS.SESSION_KEY]) {
      otherStorage.removeItem(key);
    }
    localStorage.setItem(AUTH_KEYS.REMEMBER_ME, String(rememberMe));
  }

  /** HTTP 和主动续期共享刷新；旧会话的成功/失败都不得修改新会话。 */
  static refreshTokens(sessionKey = this.getSessionKey()): Promise<void> {
    if (!sessionKey || sessionKey !== this.getSessionKey()) {
      return Promise.reject(new Error("登录会话已变更"));
    }
    const existing = refreshPromises.get(sessionKey);
    if (existing) return existing;

    const refreshToken = this.getRefreshToken();
    const rememberMe = this.getRememberMe();
    const refreshing = (async () => {
      if (!refreshToken) throw new Error("没有有效的刷新令牌");
      const { default: AuthAPI } = await import("@/api/module_system/auth");
      if (sessionKey !== this.getSessionKey()) throw new Error("登录会话已变更");
      const response = await AuthAPI.refreshToken({ refresh_token: refreshToken });
      if (sessionKey !== this.getSessionKey()) throw new Error("登录会话已变更");
      const data = response.data.data;
      if (!data?.access_token || !data.refresh_token) throw new Error("刷新令牌响应无效");
      this.storeTokens(data.access_token, data.refresh_token, rememberMe, sessionKey);
    })().finally(() => refreshPromises.delete(sessionKey));
    refreshPromises.set(sessionKey, refreshing);
    return refreshing;
  }

  static clearAuth(): void {
    localStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
    localStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
    sessionStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
    sessionStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
    localStorage.removeItem(AUTH_KEYS.SESSION_KEY);
    sessionStorage.removeItem(AUTH_KEYS.SESSION_KEY);
  }

  static getRememberMe(): boolean {
    return localStorage.getItem(AUTH_KEYS.REMEMBER_ME) === "true";
  }
}

export { AUTH_KEYS };

import { ElMessage, ElNotification } from "element-plus";

/** 登录页跳转进行中，合并并发调用，避免重复通知与重复路由 */
const loginRedirects = new Map<string, Promise<void>>();

/**
 * 认证失效或需重新登录时跳转登录页：清空本地会话并带上 redirect。
 * 与 HTTP 拦截器、改密后重登等场景共用；并发只执行一次。
 */
export async function redirectToLogin(
  message: string = "请重新登录",
  sessionKey = Auth.getSessionKey()
): Promise<void> {
  if (sessionKey !== Auth.getSessionKey()) return;
  const existing = loginRedirects.get(sessionKey);
  if (existing) return existing;

  const redirect = (async () => {
    try {
      const [{ router }, { useUserStore }, { resetDynamicRoutesSync }] = await Promise.all([
        import("@/router/instance"),
        import("@/store/modules/user.store"),
        import("@/router/beforeEach"),
      ]);
      if (sessionKey !== Auth.getSessionKey()) return;

      ElNotification({
        title: "提示",
        message,
        type: "warning",
        duration: 3000,
      });

      const currentPath = router.currentRoute.value.fullPath;
      useUserStore().resetAllState();
      resetDynamicRoutesSync();
      const target =
        currentPath && !currentPath.startsWith("/login")
          ? `/login?redirect=${encodeURIComponent(currentPath)}`
          : "/login";
      if (router.currentRoute.value.path !== "/login") {
        await router.push(target);
      }
    } catch (error: any) {
      ElMessage.error(error?.message ?? String(error));
    } finally {
      loginRedirects.delete(sessionKey);
    }
  })();
  loginRedirects.set(sessionKey, redirect);

  return redirect;
}
