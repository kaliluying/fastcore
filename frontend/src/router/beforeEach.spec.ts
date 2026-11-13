import { expect, test, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

const state = vi.hoisted(() => ({
  registrations: 0,
  registered: false,
  user: { isLogin: true, hasGetRoute: true, resetAllState: vi.fn() },
  menu: { menuList: [], setMenuList: vi.fn() },
}));

vi.mock("@stores", () => ({
  useSettingsStore: () => ({ showNprogress: false }),
  useUserStore: () => state.user,
  useMenuStore: () => state.menu,
  useWorktabStore: () => ({ validateWorktabs: vi.fn() }),
}));
vi.mock("@utils", () => ({
  NProgress: { start: vi.fn() },
  setWorktab: vi.fn(),
  setPageTitle: vi.fn(),
  loadingService: { showLoading: vi.fn(), hideLoading: vi.fn() },
  ApiStatus: { unauthorized: 401 },
  isHttpError: () => false,
}));
vi.mock("@/hooks/core/useCommon", () => ({ useCommon: () => ({ homePath: { value: "/home" } }) }));
vi.mock("./MenuProcessor", () => ({
  MenuProcessor: class {
    getMenuList() {
      return Promise.resolve([]);
    }
    validateMenuList() {
      return false;
    }
  },
}));
vi.mock("./core", () => ({
  RouteRegistry: class {
    register() {
      state.registrations++;
      state.registered = true;
    }
    unregister() {
      state.registered = false;
    }
    isRegistered() {
      return state.registered;
    }
  },
}));
vi.mock("./staticRoutes", () => ({
  ROUTE_PATH_LOGIN_ALT: "/auth/login",
  staticRoutes: [
    { path: "/home", name: "Home" },
    { path: "/profile", name: "Profile" },
  ],
}));

import { resetDynamicRoutesSync, setupBeforeEachGuard } from "./beforeEach";
import { Auth } from "@/utils/auth";

test("empty menus allow navigation and reinitialize only after an explicit reset", async () => {
  Auth.setTokens("access", "refresh", false);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ["home", "profile"].map((path) => ({
      path: `/${path}`,
      name: path,
      component: { render: () => null },
    })),
  });
  let passes = 0;
  router.beforeEach(() => ++passes <= 10); // 限制导航次数，使回归问题直接失败，避免测试一直挂起。
  setupBeforeEachGuard(router);
  await router.push("/home");
  expect(router.currentRoute.value.path).toBe("/home");
  await router.push("/profile");
  expect(router.currentRoute.value.path).toBe("/profile");
  expect(state.registrations).toBe(1);

  resetDynamicRoutesSync();
  await router.push("/home");
  expect(router.currentRoute.value.path).toBe("/home");
  expect(state.registrations).toBe(2);
});
