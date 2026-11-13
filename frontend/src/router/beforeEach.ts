/**
 * 路由前置守卫 —— 导航生命周期中的核心编排器。
 *
 * ── 职责 ──
 * 1. 存储失效检测（storage 异常时登出）
 * 2. 登录态校验 & 未登录重定向
 * 3. 动态路由延迟注册（fetch 菜单 → addRoute → 保存）
 * 4. 根路径 `/` → 首页重定向
 * 5. 工作标签同步、页面标题设置
 * 6. 404 / 500 降级兜底
 *
 * ── 核心流程 ──
 * setupBeforeEachGuard() → 注册 `router.beforeEach`
 *   └─ handleRouteGuard()   ← 单一编排入口，按优先级顺序执行
 *        ├─ checkStorageInvalidated()
 *        ├─ handleLoginStatus()
 *        ├─ routeInitFailed 兜底
 *        ├─ handleDynamicRoutes()  ← 按需拉菜单 + addRoute
 *        ├─ handleRootPathRedirect()
 *        └─ setWorktab / setPageTitle / 404
 */
import type { AppRouteRecord } from "@/types/router";
import type { Router, RouteLocationNormalized } from "vue-router";
import { nextTick } from "vue";
import { useSettingsStore, useUserStore, useMenuStore, useWorktabStore } from "@stores";
import { ROUTE_PATH_LOGIN_ALT, staticRoutes } from "./staticRoutes";
import { useCommon } from "@/hooks/core/useCommon";
import {
  NProgress,
  setWorktab,
  setPageTitle,
  loadingService,
  ApiStatus,
  isHttpError,
} from "@utils";
import { RouteRegistry } from "./core";
import { MenuProcessor } from "./MenuProcessor";
import { Auth } from "@/utils/auth";

// --- 模块级单例与守卫状态 ---

/** 动态路由注册表（惰性创建，首次导航时生成） */
let routeRegistry: RouteRegistry | null = null;

/** 菜单数据处理器（不含注册逻辑，只做列表拉取 + 树形组装） */
const menuProcessor = new MenuProcessor();

/**
 * 全局 loading 开关 —— 动态路由初始化时由 beforeEach 开启，
 * afterEach 收到标志后关闭。
 */
let pendingLoading = false;

/**
 * 路由初始化失败标记 —— 动态路由拉取/注册抛出异常后置为 true，
 * 随后所有导航直接走 500 兜底，避免反复请求造成死循环。
 * `resetRouteInitState()` 可在重新登录后重置。
 */
let routeInitFailed = false;

/**
 * 路由初始化进行中标记 —— 防止并发导航下多次拉取菜单。
 * 第二次导航 `next(false)` 取消，由首次初始化完成后重新恢复。
 */
let routeInitInProgress = false;
let routeInitGeneration = 0;
let routeSessionKey = "";

export function getPendingLoading(): boolean {
  return pendingLoading;
}

export function resetPendingLoading(): void {
  pendingLoading = false;
}

export function getRouteInitFailed(): boolean {
  return routeInitFailed;
}

/** 重新登录等场景重置初始化标记 */
export function resetRouteInitState(): void {
  routeInitGeneration++;
  routeInitFailed = false;
  routeInitInProgress = false;
  if (pendingLoading) {
    loadingService.hideLoading();
    pendingLoading = false;
  }
}

/** 防止 dev/HMR 或异常重复 init 导致多个 beforeEach 叠加（导航副作用与请求会成倍增长） */
let beforeEachGuardRegistered = false;

export function setupBeforeEachGuard(router: Router): void {
  if (beforeEachGuardRegistered) {
    if (import.meta.env.DEV) {
      console.warn("[Router] setupBeforeEachGuard 已注册，跳过重复调用");
    }
    return;
  }
  beforeEachGuardRegistered = true;

  routeRegistry = new RouteRegistry(router);

  router.beforeEach(async (to: RouteLocationNormalized, from: RouteLocationNormalized) => {
    try {
      return await handleRouteGuard(to, from, router);
    } catch (error) {
      console.error("[RouteGuard] 路由守卫处理失败:", error);
      closeLoading();
      return { name: "500" };
    }
  });
}

function closeLoading(): void {
  if (pendingLoading) {
    const generation = routeInitGeneration;
    nextTick(() => {
      if (generation !== routeInitGeneration) return;
      loadingService.hideLoading();
      pendingLoading = false;
    });
  }
}

/**
 * 若路由带有 query/params 传入的 title，写入 meta（净化防注入与过长字符串）
 */
function applySafeTitleFromQuery(to: RouteLocationNormalized): void {
  const rawTitle = (to.params.title as string) || (to.query.title as string);
  if (rawTitle && typeof rawTitle === "string") {
    const safe = rawTitle.replace(/[<>]/g, "").trim().slice(0, 64);
    if (safe) {
      to.meta.title = safe;
    }
  }
}

async function handleRouteGuard(
  to: RouteLocationNormalized,
  _from: RouteLocationNormalized,
  router: Router
) {
  // 顺序：登录 → 动态路由初始化失败兜底 → 动态路由注册 → 根路径 → 已匹配页 → 404
  const settingStore = useSettingsStore();
  const userStore = useUserStore();
  // 新标签页可能只继承 localStorage 的登录标记，未继承 sessionStorage 凭证。
  if (userStore.isLogin && !Auth.isLoggedIn()) {
    userStore.resetAllState();
    resetDynamicRoutesSync();
  }
  if (
    (routeRegistry?.isRegistered() || routeInitInProgress) &&
    routeSessionKey !== Auth.getSessionKey()
  ) {
    resetDynamicRoutesSync();
  }

  if (settingStore.showNprogress) {
    NProgress.start();
  }

  const loginRedirect = handleLoginStatus(to, userStore);
  if (loginRedirect) return loginRedirect;

  if (routeInitFailed) {
    return to.matched.length > 0 ? true : { name: "500", replace: true };
  }

  const shouldInitRoutes = userStore.isLogin && !routeRegistry?.isRegistered();

  if (shouldInitRoutes) {
    if (routeInitInProgress) return false;
    return await handleDynamicRoutes(to, router);
  }

  const rootRedirect = handleRootPathRedirect(to);
  if (rootRedirect) return rootRedirect;

  if (to.matched.length > 0) {
    applySafeTitleFromQuery(to);
    setWorktab(to);
    setPageTitle(to);
    return true;
  }

  return { name: "404" };
}

/** @returns undefined 继续守卫，或重定向到登录页的路由 */
function handleLoginStatus(
  to: RouteLocationNormalized,
  userStore: ReturnType<typeof useUserStore>
): Record<string, unknown> | undefined {
  if (userStore.isLogin) {
    if (isLoginRoute(to)) {
      return { path: "/", replace: true };
    }
    return undefined;
  }

  if (isLoginRoute(to) || isAnonymousPublicPath(to.path)) {
    return undefined;
  }

  userStore.resetAllState();
  return { name: "Login", query: { redirect: to.fullPath }, replace: true };
}

/** 登录页（项目里同时存在 `/login` 与 `/auth/login` 等多套入口） */
function isLoginRoute(to: RouteLocationNormalized): boolean {
  return to.path === "/login" || to.path === ROUTE_PATH_LOGIN_ALT || to.name === "Login";
}

/**
 * 无需登录即可访问的路径（登录页由 isLoginRoute 处理，此处为错误页、重定向等）。
 * 勿将挂载 Layout 的业务路由列入此处。
 */
function isAnonymousPublicPath(path: string): boolean {
  if (path.startsWith("/redirect")) return true;
  const allow = new Set(["/401", "/404", "/500", "/403"]);
  return allow.has(path);
}

/** 将父级绝对路径与相对子 path 拼成完整路径。 */
function resolveStaticChildFullPath(parentFullPath: string, segment: string): string {
  const seg = segment.replace(/^\/+/, "");
  if (!parentFullPath || parentFullPath === "/") {
    return `/${seg}`;
  }
  return `${parentFullPath.replace(/\/$/, "")}/${seg}`;
}

let cachedStaticRoutesRef: any[] | null = null;
let cachedStaticRouteRegexes: RegExp[] = [];

function getStaticRouteRegexes(): RegExp[] {
  if (cachedStaticRoutesRef === staticRoutes) {
    return cachedStaticRouteRegexes;
  }
  cachedStaticRoutesRef = staticRoutes;
  const regexes: RegExp[] = [];

  const extractPatterns = (routes: any[], parentFullPath = "") => {
    for (const route of routes) {
      if (route.path === "/:pathMatch(.*)*") continue;
      const routePath = route.path ?? "";
      const fullPath = routePath.startsWith("/")
        ? routePath
        : resolveStaticChildFullPath(parentFullPath, routePath);
      const pattern = fullPath.replace(/:[^/]+/g, "[^/]+").replace(/\*/g, ".*");
      regexes.push(new RegExp(`^${pattern}$`));
      if (route.children && route.children.length > 0) {
        extractPatterns(route.children, fullPath);
      }
    }
  };

  extractPatterns(staticRoutes);
  cachedStaticRouteRegexes = regexes;
  return regexes;
}

/**
 * 检查路由是否为静态路由
 */
function isStaticRoute(path: string): boolean {
  return getStaticRouteRegexes().some((regex) => regex.test(path));
}

/**
 * 处理动态路由注册
 */
async function handleDynamicRoutes(to: RouteLocationNormalized, router: Router) {
  const sessionKey = Auth.getSessionKey();
  const generation = routeInitGeneration;
  const isCurrent = () => generation === routeInitGeneration && sessionKey === Auth.getSessionKey();
  // 标记初始化进行中
  routeInitInProgress = true;
  routeSessionKey = sessionKey;

  // 显示 loading
  pendingLoading = true;
  loadingService.showLoading();

  try {
    // 1. 获取用户信息（若 login() 已拉取则跳过，避免重复请求）
    const userStore = useUserStore();
    if (!userStore.hasGetRoute) {
      await fetchUserInfo();
    }
    if (!isCurrent()) return false;

    // 2. 获取菜单数据
    const menuList = await menuProcessor.getMenuList();
    if (!isCurrent()) return false;

    // 3. 验证菜单数据（空菜单不阻塞：用户可能未分配角色，允许进入首页）
    const menuValid = menuProcessor.validateMenuList(menuList);
    if (!menuValid) {
      console.warn("[RouteGuard] 菜单列表为空，用户可能未分配角色，使用空菜单继续导航");
    }

    // 4. 注册动态路由
    routeRegistry?.register(menuList);

    // 5. 保存菜单数据到 store
    const menuStore = useMenuStore();
    menuStore.setMenuList(menuList);

    // 6. 验证工作标签页
    useWorktabStore().validateWorktabs(router);

    // 8. 静态路由不依赖菜单权限，初始化后直接恢复目标地址。
    if (isStaticRoute(to.path)) {
      routeInitInProgress = false;
      return {
        path: to.path,
        query: to.query,
        hash: to.hash,
        replace: true,
      };
    }

    // 8. 验证目标路径权限
    const { homePath } = useCommon();
    const { path: validatedPath, hasPermission } = RoutePermissionValidator.validatePath(
      to.path,
      menuList,
      homePath.value || "/"
    );

    // 初始化成功，重置进行中标记
    routeInitInProgress = false;

    // 9. 重新导航到目标路由
    if (!hasPermission) {
      closeLoading();
      console.warn(`[RouteGuard] 用户无权限访问路径: ${to.path}，已跳转到首页`);
      return { path: validatedPath, replace: true };
    }

    return { path: to.path, query: to.query, hash: to.hash, replace: true };
  } catch (error) {
    if (!isCurrent()) return false;
    console.error("[RouteGuard] 动态路由注册失败:", error);
    closeLoading();

    // 401 错误：axios 拦截器已处理退出登录，取消当前导航
    if (isUnauthorizedError(error)) {
      routeInitInProgress = false;
      return false;
    }

    routeInitFailed = true;
    routeInitInProgress = false;

    if (isHttpError(error)) {
      console.error(`[RouteGuard] 错误码: ${error.code}, 消息: ${error.message}`);
    }

    return { name: "500", replace: true };
  }
}

/**
 * 获取用户信息
 */
async function fetchUserInfo(): Promise<void> {
  const userStore = useUserStore();
  await userStore.getUserInfo();
  userStore.checkAndClearWorktabs();
}

/**
 * 立即卸下动态路由并重置菜单和初始化状态，供登出、认证失效与刷新使用。
 */
export function resetDynamicRoutesSync(): void {
  routeRegistry?.unregister();
  routeSessionKey = "";
  useMenuStore().setMenuList([]);

  resetRouteInitState();
}

/**
 * 刷新动态路由和菜单：卸载旧路由 → 重新拉取菜单 → 注册新路由。
 * 不拉取用户信息（由调用方并行完成），避免覆盖前端本地状态（主题色等）。
 */
export async function refreshMenuAndRoutes(): Promise<void> {
  resetDynamicRoutesSync();
  const sessionKey = Auth.getSessionKey();
  const generation = routeInitGeneration;
  const menuStore = useMenuStore();

  const menuList = await menuProcessor.getMenuList();
  if (generation !== routeInitGeneration || sessionKey !== Auth.getSessionKey()) return;
  routeRegistry?.register(menuList);
  routeSessionKey = sessionKey;
  menuStore.setMenuList(menuList);
}

/**
 * 处理根路径重定向到首页
 * @returns 重定向路由或 false
 */
function handleRootPathRedirect(to: RouteLocationNormalized): Record<string, unknown> | false {
  if (to.path !== "/") return false;

  const { homePath } = useCommon();
  if (homePath.value && homePath.value !== "/") {
    return { path: homePath.value, replace: true };
  }

  return false;
}

/**
 * 判断是否为未授权错误（401）
 */
function isUnauthorizedError(error: unknown): boolean {
  return isHttpError(error) && error.code === ApiStatus.unauthorized;
}

/** 守卫内菜单路径权限校验（扁平菜单路径集合） */
export class RoutePermissionValidator {
  static hasPermission(targetPath: string, menuList: AppRouteRecord[]): boolean {
    if (targetPath === "/") {
      return true;
    }
    return this.matchRoute(targetPath, menuList);
  }

  static matchRoute(targetPath: string, routes: AppRouteRecord[]): boolean {
    if (!Array.isArray(routes) || routes.length === 0) {
      return false;
    }

    for (const route of routes) {
      if (!route.path) {
        continue;
      }

      const routePath = route.path.startsWith("/") ? route.path : `/${route.path}`;

      if (
        routePath === targetPath ||
        this.isDynamicRouteMatch(targetPath, routePath) ||
        targetPath.startsWith(`${routePath}/`)
      ) {
        return true;
      }

      if (route.children?.length && this.matchRoute(targetPath, route.children)) {
        return true;
      }
    }

    return false;
  }

  static isDynamicRouteMatch(targetPath: string, routePath: string): boolean {
    if (!routePath.includes(":")) {
      return false;
    }

    const pattern = routePath
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      .replace(/:([^/]+)/g, "[^/]+")
      .replace(/\\\*/g, ".*");

    return new RegExp(`^${pattern}$`).test(targetPath);
  }

  static validatePath(
    targetPath: string,
    menuList: AppRouteRecord[],
    homePath: string = "/"
  ): { path: string; hasPermission: boolean } {
    const hasPermission = this.hasPermission(targetPath, menuList);

    if (hasPermission) {
      return { path: targetPath, hasPermission: true };
    }

    return { path: homePath, hasPermission: false };
  }
}
