/** 用户状态管理。 */
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { LanguageEnum } from "@/enums/appEnum";
import { router } from "@/router/instance";
import { useSettingsStore } from "./setting.store";
import { useWorktabStore } from "./worktab.store";
import { useMenuStore } from "./menu.store";
import { AppRouteRecord } from "@/types/router";
import { Auth } from "@/utils/auth";
import { setPageTitle } from "@/utils/navigation";
import { StorageConfig } from "@/utils/storage";
import AuthAPI from "@/api/module_system/auth";
import UserAPI from "@/api/module_system/user";
import type { MenuTable } from "@/api/module_platform/menu";
import { ResultEnum } from "@/enums/api/result.enum";
import { ElNotification } from "element-plus";
import { store } from "../pinia";
import type { UserInfo } from "@/api/module_system/user";
import { USER_STORE_PERSISTED_FIELDS, USER_STORE_PERSIST_KEY } from "./userPersistence";

/** 延迟导入路由守卫辅助函数，避免状态模块与守卫之间的循环依赖。 */
let _routerUtilsPromise: Promise<typeof import("@/router/beforeEach")> | null = null;
const getRouterUtils = () => {
  if (!_routerUtilsPromise) _routerUtilsPromise = import("@/router/beforeEach");
  return _routerUtilsPromise;
};

/** {@link useUserStore} 的 `logout` 选项。 */
export interface LogoutOptions {
  /**
   * 为 false 时仅清理本地认证状态，由调用方负责导航。
   * @default true
   */
  navigate?: boolean;
}

/** 用户状态：认证令牌、用户信息、权限和动态路由。 */
export const useUserStore = defineStore(
  "userStore",
  () => {
    // 语言设置。
    const language = ref(LanguageEnum.ZH);
    // 登录状态。
    const isLogin = ref(false);
    // 用户信息。
    const info = ref<Partial<UserInfo>>({});
    // 搜索历史记录。
    const searchHistory = ref<AppRouteRecord[]>([]);
    // 访问令牌。
    const accessToken = ref("");
    // 刷新令牌。
    const refreshToken = ref("");
    // 路由列表。
    const routeList = ref<MenuTable[]>([]);
    // 权限列表。
    const prems = ref<string[]>([]);
    // 是否已获取动态路由。
    const hasGetRoute = ref(false);
    // 记住我状态。
    const rememberMe = ref(Auth.getRememberMe());
    let userInfoRequest = 0;

    // 基础用户信息。
    const basicInfo = computed(() => info.value);
    // 设置状态。
    const getSettingState = computed(() => useSettingsStore().$state);
    // 工作台状态。
    const getWorktabState = computed(() => useWorktabStore().$state);
    // 获取基础用户信息。
    const getBasicInfo = computed(() => info.value);
    // 获取路由列表。
    const getRouteList = computed(() => routeList.value);
    // 获取权限列表。
    const getPerms = computed(() => prems.value);
    // 获取动态路由加载状态。
    const getHasGetRoute = computed(() => hasGetRoute.value);

    /**
     * 设置用户信息。
     * @param newInfo 新的用户信息。
     */
    const setUserInfo = (newInfo: UserInfo) => {
      info.value = newInfo;
      // 用户信息变更后刷新权限。
      setPermissions();
    };

    /**
     * 设置登录状态。
     * @param status 登录状态。
     */
    const setLoginStatus = (status: boolean) => {
      isLogin.value = status;
    };

    /**
     * 设置语言。
     * @param lang 语言枚举值。
     */
    const setLanguage = (lang: LanguageEnum) => {
      setPageTitle(router.currentRoute.value);
      language.value = lang;
    };

    /**
     * 设置搜索历史。
     * @param list 搜索历史列表。
     */
    const setSearchHistory = (list: AppRouteRecord[]) => {
      searchHistory.value = list;
    };

    /**
     * 设置认证令牌。
     * @param newAccessToken 访问令牌。
     * @param newRefreshToken 可选的刷新令牌。
     */
    const setToken = (newAccessToken: string, newRefreshToken?: string) => {
      accessToken.value = newAccessToken;
      if (newRefreshToken) {
        refreshToken.value = newRefreshToken;
      }
    };

    /**
     * 检查并清理工作台标签页。
     * 当登录用户发生变化时清空旧用户的工作台标签页，登录成功后调用。
     */
    const checkAndClearWorktabs = () => {
      const lastUserId = localStorage.getItem(StorageConfig.LAST_USER_ID_KEY);
      const currentUserId = info.value?.id;

      // 无法获取当前用户 ID 时跳过检查。
      if (!currentUserId) return;

      // 首次登录或缓存已清理时，保留当前标签页。
      if (!lastUserId) {
        return;
      }

      // 不同用户登录时，清空工作台标签页，包括当前标签页。
      if (String(currentUserId) !== String(lastUserId)) {
        useWorktabStore().clearAll();
      }

      // 清理临时存储。
      localStorage.removeItem(StorageConfig.LAST_USER_ID_KEY);
    };

    /**
     * 获取当前用户信息。
     */
    async function getUserInfo() {
      const sessionKey = Auth.getSessionKey();
      const requestId = ++userInfoRequest;
      try {
        const response = await UserAPI.getCurrentUserInfo();
        if (!sessionKey || sessionKey !== Auth.getSessionKey() || requestId !== userInfoRequest) {
          throw new Error("用户信息请求已过期");
        }
        const data = response.data.data;
        const menus: MenuTable[] = data?.menus || [];
        delete data?.menus;
        const previousIsSuperuser = info.value.is_superuser;
        info.value = {
          ...info.value,
          ...data,
          is_superuser: data?.is_superuser ?? previousIsSuperuser,
        } as Partial<UserInfo>;
        setRoute(menus);
      } catch (error) {
        if (sessionKey === Auth.getSessionKey() && requestId === userInfoRequest) {
          console.error("获取用户信息失败:", error);
        }
        throw error;
      }
    }

    /**
     * 设置头像。
     */
    function setAvatar(avatar: string) {
      info.value = { ...info.value, avatar };
    }

    /**
     * 设置路由。
     */
    function setRoute(routers: MenuTable[]) {
      routeList.value = routers;
      hasGetRoute.value = true;
      setPermissions();
    }

    /**
     * 设置权限。
     */
    function setPermissions() {
      const permissions = info.value.permissions ?? [];
      prems.value = Array.from(new Set(permissions.filter(Boolean)));
    }

    /**
     * 登录。
     */
    async function login(loginForm: any) {
      const response = await AuthAPI.login(loginForm);
      const data = response.data.data;
      if (response.data.code === ResultEnum.SUCCESS) {
        ElNotification({
          title: "登录成功",
          message: response.data.msg,
          type: "success",
        });
      }
      rememberMe.value = loginForm.remember;

      const accessToken = data?.access_token || "";
      const refreshToken = data?.refresh_token || "";
      if (!accessToken) {
        console.error("[Login Debug] 未获取到 access_token，可能是后端响应字段不匹配");
      }

      // 重建路由前清理上一会话残留的路由和菜单状态。
      useMenuStore().setMenuList([]);
      useMenuStore().setHomePath("");
      (await getRouterUtils()).resetDynamicRoutesSync();
      clearUserInfo();
      prems.value = [];
      setLoginStatus(false);
      Auth.setTokens(accessToken, refreshToken, rememberMe.value);
      const sessionKey = Auth.getSessionKey();
      setToken(accessToken, refreshToken);
      if (data?.user_info) {
        info.value = { ...info.value, ...data.user_info } as Partial<UserInfo>;
      }

      await getUserInfo();
      if (sessionKey !== Auth.getSessionKey()) throw new Error("登录会话已变更");
      setLoginStatus(true);
    }

    /**
     * 登出：有 token 时请求后端，清理本地状态，并按需跳转到登录页。
     */
    async function logout(options?: LogoutOptions) {
      const shouldNavigate = options?.navigate !== false;

      const currentUserId = info.value?.id;
      if (currentUserId) {
        localStorage.setItem(StorageConfig.LAST_USER_ID_KEY, String(currentUserId));
      }

      let apiError: unknown;
      const token = Auth.getAccessToken();
      const logoutRequest = token
        ? AuthAPI.logout().catch((error) => {
            apiError = error;
          })
        : undefined;
      // 请求已绑定旧凭证；先注销本地会话，避免等待网络期间的刷新使会话复活。
      resetAllState();
      const routerUtils = await getRouterUtils();
      if (!Auth.getSessionKey()) routerUtils.resetDynamicRoutesSync();
      if (logoutRequest) {
        try {
          const response = await logoutRequest;
          if (response?.data.code === ResultEnum.SUCCESS && !Auth.getSessionKey()) {
            ElNotification({
              title: "退出成功",
              message: response.data.msg,
              type: "success",
            });
          }
        } catch (e) {
          apiError = e;
        }
      }

      if (shouldNavigate && !Auth.getSessionKey()) {
        const currentRoute = router.currentRoute.value;
        const redirect = currentRoute.path !== "/login" ? currentRoute.fullPath : undefined;
        await router.push({
          name: "Login",
          query: redirect ? { redirect } : undefined,
        });
      }

      if (apiError) {
        throw apiError;
      }
    }

    /**
     * 重置所有认证相关状态。
     */
    function resetAllState() {
      userInfoRequest++;
      Auth.clearAuth();
      info.value = {};
      routeList.value = [];
      hasGetRoute.value = false;
      isLogin.value = false;
      accessToken.value = "";
      refreshToken.value = "";
      prems.value = [];
      // 登出或认证失效时，同时清理会话标签页和 KeepAlive 状态。
      useWorktabStore().clearAll();
    }

    /**
     * 清空用户信息。
     */
    function clearUserInfo() {
      userInfoRequest++;
      info.value = {};
      routeList.value = [];
      hasGetRoute.value = false;
    }

    /**
     * 刷新 token。
     */
    async function refreshTokenFn() {
      const sessionKey = Auth.getSessionKey();
      await Auth.refreshTokens(sessionKey);
      if (sessionKey !== Auth.getSessionKey()) throw new Error("登录会话已变更");
      setToken(Auth.getAccessToken(), Auth.getRefreshToken());
    }

    /**
     * 完全重置所有状态，包括路由、标签页和字典。
     */
    function fullResetAllState() {
      resetAllState();
      return Promise.resolve();
    }

    return {
      language,
      isLogin,
      info,
      searchHistory,
      accessToken,
      routeList,
      prems,
      hasGetRoute,
      rememberMe,
      getUserInfo,
      getSettingState,
      getWorktabState,
      basicInfo,
      getBasicInfo,
      getRouteList,
      getPerms,
      getHasGetRoute,
      setUserInfo,
      setLoginStatus,
      setLanguage,
      setSearchHistory,
      setToken,
      setAvatar,
      setRoute,
      setPermissions,
      login,
      logout,
      checkAndClearWorktabs,
      clearUserInfo,
      refreshTokenFn,
      resetAllState,
      fullResetAllState,
    };
  },
  {
    persist: {
      key: USER_STORE_PERSIST_KEY,
      storage: localStorage,
      // accessToken/refreshToken 由 Auth 类按 rememberMe 分别存入 localStorage/sessionStorage，
      // 这里不重复持久化，否则 rememberMe=false 时 token 仍会残留在 localStorage["user"] 里。
      // 动态路由必须随当前用户信息重新从后端获取，不能跨刷新复用旧项目菜单。
      pick: USER_STORE_PERSISTED_FIELDS,
      afterHydrate: ({ store }) => {
        const hydratedState = store.$state as { routeList: MenuTable[]; hasGetRoute: boolean };
        hydratedState.routeList = [];
        hydratedState.hasGetRoute = false;
      },
    },
  }
);

export function useUserStoreHook() {
  return useUserStore(store);
}
