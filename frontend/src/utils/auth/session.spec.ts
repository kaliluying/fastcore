import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("element-plus", () => ({
  ElNotification: vi.fn(),
  ElMessage: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("@/locales", () => ({ $t: (key: string) => key }));
vi.mock("@/utils/navigation", () => ({ setPageTitle: vi.fn() }));
vi.mock("@/utils/storage", () => ({
  StorageConfig: { LAST_USER_ID_KEY: "last-user" },
}));
vi.mock("@/store/modules/setting.store", () => ({
  useSettingsStore: () => ({ showNprogress: false }),
}));
vi.mock("@/hooks/core/useCommon", () => ({
  useCommon: () => ({ homePath: { value: "/home" } }),
}));
vi.mock("@stores", async () => ({
  ...(await import("@/store/modules/user.store")),
  ...(await import("@/store/modules/menu.store")),
  ...(await import("@/store/modules/worktab.store")),
  useSettingsStore: () => ({ showNprogress: false }),
}));
vi.mock("@utils", async () => ({
  ...(await import("@/utils/http")),
  NProgress: { start: vi.fn() },
  setWorktab: vi.fn(),
  setPageTitle: vi.fn(),
  loadingService: { showLoading: vi.fn(), hideLoading: vi.fn() },
}));
// Only view loading is stubbed; guards, menu conversion and route registration run unchanged.
vi.mock("@/router/core/ComponentLoader", () => ({
  ComponentLoader: class {
    load() {
      return () => Promise.resolve({ render: () => null });
    }
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

type Pending = {
  config: InternalAxiosRequestConfig;
  resolve: (response: AxiosResponse) => void;
  reject: (reason: unknown) => void;
};
let pending: Pending[];
let calls: InternalAxiosRequestConfig[];
let Auth: typeof import("./index").Auth;
let redirectToLogin: typeof import("./index").redirectToLogin;
let request: typeof import("@/utils/http").request;
let user: ReturnType<typeof import("@/store/modules/user.store").useUserStore>;
let router: typeof import("@/router/instance").router;
let guards: typeof import("@/router/beforeEach");

function succeed(job: Pending, data: unknown = {}) {
  job.resolve({
    config: job.config,
    status: 200,
    statusText: "OK",
    headers: {},
    data: { code: 0, msg: "ok", data },
  });
}

function unauthorized(job: Pending) {
  const response: AxiosResponse = {
    config: job.config,
    status: 401,
    statusText: "Unauthorized",
    headers: {},
    data: { code: 10401, msg: "expired" },
  };
  job.reject(new AxiosError("expired", "ERR_BAD_REQUEST", job.config, undefined, response));
}

async function take(url: string) {
  await vi.waitFor(() => expect(pending.some((job) => job.config.url === url)).toBe(true));
  return pending.splice(
    pending.findIndex((job) => job.config.url === url),
    1
  )[0]!;
}

function tokenData(account: string) {
  return { access_token: `${account}-access`, refresh_token: `${account}-refresh` };
}

function userData(account: string) {
  return {
    id: account === "A" ? 1 : 2,
    username: account,
    permissions: [`${account}:read`],
    menus: [
      {
        id: account === "A" ? 1 : 2,
        title: account,
        route_name: `${account}Page`,
        route_path: `/${account}`,
        component_path: "/home/index",
        type: 2,
      },
    ],
  };
}

function beginSession(account: string, remember = false) {
  Auth.setTokens(`${account}-access`, `${account}-refresh`, remember);
  user.setLoginStatus(true);
}

async function loadUser(account: string) {
  const loading = user.getUserInfo();
  succeed(await take("/system/user/current/info"), userData(account));
  await loading;
}

beforeEach(async () => {
  vi.restoreAllMocks();
  vi.resetModules();
  localStorage.clear();
  sessionStorage.clear();
  setActivePinia(createPinia());
  pending = [];
  calls = [];
  const { createRouter, createMemoryHistory } = await import("vue-router");
  const { ROOT_LAYOUT_ROUTE_NAME } = await import("@/router/staticRoutes");
  const component = { render: () => null };
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/",
        name: ROOT_LAYOUT_ROUTE_NAME,
        component,
        children: [{ path: "home", name: "Home", component }],
      },
      { path: "/login", name: "Login", component },
      { path: "/404", name: "404", component },
      { path: "/500", name: "500", component },
    ],
  });
  vi.doMock("@/router/instance", () => ({ router }));
  vi.doMock("@/router", () => ({ router }));
  ({ Auth, redirectToLogin } = await import("./index"));
  ({ request } = await import("@/utils/http"));
  ({ router } = await import("@/router/instance"));
  user = (await import("@/store/modules/user.store")).useUserStore();
  guards = await import("@/router/beforeEach");
  request.defaults.adapter = (config) => {
    calls.push(config);
    return new Promise((resolve, reject) => pending.push({ config, resolve, reject }));
  };
  guards.setupBeforeEachGuard(router);
});

it.each(["/home", "/login"])(
  "recovers persisted login state without tab credentials when opening %s",
  async (target) => {
    // A new tab shares the persisted login flag, but not unremembered credentials.
    user.setLoginStatus(true);
    const navigation = router.push(target);
    await vi.waitFor(() =>
      expect(
        router.currentRoute.value.path === "/login" ||
          pending.some((job) => job.config.url === "/system/user/current/info")
      ).toBe(true)
    );
    const info = pending.find((job) => job.config.url === "/system/user/current/info");
    if (info) unauthorized(info);
    expect(user.isLogin).toBe(false);
    await navigation;
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.matched.length).toBeGreaterThan(0);
    expect(calls).toHaveLength(0);
    expect(guards.getRouteInitFailed()).toBe(false);
  }
);

it("rejects A's delayed 401 after B logs in without refreshing or replaying A's operation", async () => {
  beginSession("A");
  let settled = false;
  const result = request.post("/operation", { owner: "A" }).catch((error) => {
    settled = true;
    return error;
  });
  const oldRequest = await take("/operation");
  beginSession("B");
  unauthorized(oldRequest);
  // Settle a wrongly issued refresh as well, so the baseline failure cannot hang.
  await vi.waitFor(() => expect(settled || pending.length > 0).toBe(true));
  const wrongRefresh = pending.find((job) => job.config.url?.includes("/token/refresh"));
  if (wrongRefresh) unauthorized(wrongRefresh);
  await result;
  expect(calls.filter((config) => config.url?.includes("/token/refresh"))).toHaveLength(0);
  expect(calls.filter((config) => config.url === "/operation")).toHaveLength(1);
  expect(Auth.getAccessToken()).toBe("B-access");
});

it("discards a refresh success after logout begins", async () => {
  beginSession("A");
  let settled = false;
  const result = request.get("/operation").catch((error) => {
    settled = true;
    return error;
  });
  unauthorized(await take("/operation"));
  const refresh = await take("/system/auth/token/refresh");
  const logout = user.logout({ navigate: false });
  const logoutRequest = await take("/system/auth/logout");
  succeed(refresh, tokenData("A-new"));
  await vi.waitFor(() =>
    expect(settled || pending.some((job) => job.config.url === "/operation")).toBe(true)
  );
  const replay = pending.find((job) => job.config.url === "/operation");
  if (replay) succeed(replay);
  await result;
  succeed(logoutRequest);
  await logout;
  expect(Auth.getAccessToken()).toBe("");
  expect(calls.filter((config) => config.url === "/operation")).toHaveLength(1);
});

it("does not let an old refresh failure log B out", async () => {
  beginSession("A");
  const result = request.get("/operation").catch((error) => error);
  unauthorized(await take("/operation"));
  const refresh = await take("/system/auth/token/refresh");
  beginSession("B");
  unauthorized(refresh);
  await result;
  expect(Auth.getAccessToken()).toBe("B-access");
  expect(user.isLogin).toBe(true);
  expect(router.currentRoute.value.path).not.toBe("/login");
});

it("shares one refresh for concurrent 401s and keeps the same session through rotation", async () => {
  beginSession("A");
  const session = Auth.getSessionKey();
  const first = request.post("/one", { owner: "A" });
  const second = request.get("/two");
  unauthorized(await take("/one"));
  unauthorized(await take("/two"));
  const refresh = await take("/system/auth/token/refresh");
  succeed(refresh, tokenData("A-new"));
  const replayOne = await take("/one");
  const replayTwo = await take("/two");
  expect(replayOne.config.headers.Authorization).toBe("Bearer A-new-access");
  expect(replayTwo.config.headers.Authorization).toBe("Bearer A-new-access");
  expect(JSON.parse(replayOne.config.data)).toEqual({ owner: "A" });
  succeed(replayOne);
  succeed(replayTwo);
  await Promise.all([first, second]);
  expect(pending).toHaveLength(0);
  expect(Auth.getSessionKey()).toBe(session);
});

it("discards A's late user info, permissions and menus after B has loaded", async () => {
  beginSession("A");
  const oldInfo = user.getUserInfo().catch((error) => error);
  const oldRequest = await take("/system/user/current/info");
  beginSession("B");
  await loadUser("B");
  succeed(oldRequest, userData("A"));
  await oldInfo;
  expect(user.info.username).toBe("B");
  expect(user.prems).toEqual(["B:read"]);
  expect(user.routeList[0]?.route_name).toBe("BPage");
});

it("only commits the newest user info request within a session", async () => {
  beginSession("A");
  const first = user.getUserInfo().catch((error) => error);
  const oldRequest = await take("/system/user/current/info");
  await loadUser("B");
  succeed(oldRequest, userData("A"));
  await first;
  expect(user.info.username).toBe("B");
  expect(user.prems).toEqual(["B:read"]);
});

it("unregisters A's routes on authentication expiry and registers B's routes", async () => {
  beginSession("A");
  await loadUser("A");
  await router.push("/A");
  expect(router.hasRoute("APage")).toBe(true);
  await redirectToLogin();
  expect(router.hasRoute("APage")).toBe(false);
  beginSession("B");
  await loadUser("B");
  await router.push("/B");
  expect(router.currentRoute.value.path).toBe("/B");
  expect(router.hasRoute("BPage")).toBe(true);
  expect(router.hasRoute("APage")).toBe(false);
});

it("does not commit an old route initialization after a session reset", async () => {
  beginSession("A");
  await loadUser("A");
  const { MenuProcessor } = await import("@/router/MenuProcessor");
  const getMenus = MenuProcessor.prototype.getMenuList;
  const gate = deferred<void>();
  let started = false;
  vi.spyOn(MenuProcessor.prototype, "getMenuList").mockImplementationOnce(async function (
    this: InstanceType<typeof MenuProcessor>
  ) {
    const menus = await getMenus.call(this);
    started = true;
    await gate.promise;
    return menus;
  });
  const navigation = router.push("/A");
  await vi.waitFor(() => expect(started).toBe(true));
  await redirectToLogin();
  beginSession("B");
  await loadUser("B");
  gate.resolve();
  await navigation;
  expect(router.hasRoute("APage")).toBe(false);
  expect(guards.getRouteInitFailed()).toBe(false);
  await router.push("/B");
  expect(router.hasRoute("BPage")).toBe(true);
});

it("does not let refreshTokenFn overwrite a new session", async () => {
  beginSession("A");
  const refreshing = user.refreshTokenFn().catch((error) => error);
  const refresh = await take("/system/auth/token/refresh");
  beginSession("B");
  succeed(refresh, tokenData("A-new"));
  await refreshing;
  expect(Auth.getAccessToken()).toBe("B-access");
});

it("gives a new session to a same-account login and restores it across reload", async () => {
  const token = `header.${btoa(JSON.stringify({ sub: "same-account" }))}.signature`;
  Auth.setTokens(token, "refresh-1", false);
  const firstSession = Auth.getSessionKey();
  Auth.setTokens(token, "refresh-2", true);
  const secondSession = Auth.getSessionKey();
  expect(secondSession).not.toBe(firstSession);
  expect(sessionStorage.getItem("access_token")).toBeNull();
  vi.resetModules();
  const { Auth: reloadedAuth } = await import("./index");
  expect(reloadedAuth.getSessionKey()).toBe(secondSession);
  reloadedAuth.clearAuth();
  expect(reloadedAuth.getSessionKey()).toBe("");
});

it("keeps A and B refreshes independent, including their waiting requests", async () => {
  beginSession("A");
  const old = request.get("/old").catch((error) => error);
  const oldQueued = request.get("/old-queued").catch((error) => error);
  unauthorized(await take("/old"));
  unauthorized(await take("/old-queued"));
  const oldRefresh = await take("/system/auth/token/refresh");
  beginSession("B");
  const current = request.get("/current");
  unauthorized(await take("/current"));
  const currentRefresh = await take("/system/auth/token/refresh");
  unauthorized(oldRefresh);
  await Promise.all([old, oldQueued]);
  expect(Auth.getAccessToken()).toBe("B-access");
  succeed(currentRefresh, tokenData("B-new"));
  const replay = await take("/current");
  expect(replay.config.headers.Authorization).toBe("Bearer B-new-access");
  succeed(replay);
  await current;
  expect(calls.filter((config) => config.url === "/old")).toHaveLength(1);
  expect(calls.filter((config) => config.url === "/old-queued")).toHaveLength(1);
});

it("reuses a renewed token for a late 401 within the same session", async () => {
  beginSession("A");
  const first = request.get("/first");
  const late = request.get("/late");
  const firstRequest = await take("/first");
  const lateRequest = await take("/late");
  unauthorized(firstRequest);
  succeed(await take("/system/auth/token/refresh"), tokenData("A-new"));
  succeed(await take("/first"));
  await first;
  unauthorized(lateRequest);
  const replay = await take("/late");
  expect(replay.config.headers.Authorization).toBe("Bearer A-new-access");
  succeed(replay);
  await late;
  expect(calls.filter((config) => config.url?.includes("/token/refresh"))).toHaveLength(1);
});

it("shares refreshTokenFn with HTTP refresh and retains session identity", async () => {
  beginSession("A", true);
  const sessionKey = Auth.getSessionKey();
  const activeRefresh = user.refreshTokenFn();
  const refreshRequest = await take("/system/auth/token/refresh");
  const operation = request.get("/operation");
  unauthorized(await take("/operation"));
  succeed(refreshRequest, tokenData("A-new"));
  succeed(await take("/operation"));
  await Promise.all([activeRefresh, operation]);
  expect(calls.filter((config) => config.url?.includes("/token/refresh"))).toHaveLength(1);
  expect(Auth.getSessionKey()).toBe(sessionKey);
  expect(localStorage.getItem("access_token")).toBe("A-new-access");
  expect(sessionStorage.getItem("access_token")).toBeNull();
  expect(user.accessToken).toBe("A-new-access");
});

it("limits a retried 401 to one refresh and unloads the expired routes", async () => {
  beginSession("A");
  await loadUser("A");
  await router.push("/A");
  const result = request.get("/operation").catch((error) => error);
  unauthorized(await take("/operation"));
  succeed(await take("/system/auth/token/refresh"), tokenData("A-new"));
  unauthorized(await take("/operation"));
  await result;
  expect(calls.filter((config) => config.url?.includes("/token/refresh"))).toHaveLength(1);
  expect(Auth.getAccessToken()).toBe("");
  expect(router.hasRoute("APage")).toBe(false);
  expect(router.currentRoute.value.path).toBe("/login");
});

it("does not let an in-flight logout clear B's login or dynamic routes", async () => {
  beginSession("A");
  const loggingOut = user.logout();
  const logoutRequest = await take("/system/auth/logout");
  expect(logoutRequest.config.headers.Authorization).toBe("Bearer A-access");
  expect(Auth.getSessionKey()).toBe("");
  beginSession("B");
  await loadUser("B");
  await router.push("/B");
  unauthorized(logoutRequest);
  await loggingOut.catch(() => {});
  expect(Auth.getAccessToken()).toBe("B-access");
  expect(user.isLogin).toBe(true);
  expect(router.hasRoute("BPage")).toBe(true);
  expect(router.currentRoute.value.path).toBe("/B");
});

it("checks the session again after loading redirect dependencies", async () => {
  beginSession("A");
  const redirect = redirectToLogin();
  beginSession("B");
  await redirect;
  expect(Auth.getAccessToken()).toBe("B-access");
  expect(user.isLogin).toBe(true);
  expect(router.currentRoute.value.path).not.toBe("/login");
});

it("replaces dynamic routes for a direct OAuth-style token login", async () => {
  beginSession("A");
  await loadUser("A");
  await router.push("/A");
  beginSession("B", true);
  await loadUser("B");
  await router.push("/B");
  expect(router.hasRoute("APage")).toBe(false);
  expect(router.hasRoute("BPage")).toBe(true);
});

it("does not allow an expired menu refresh to register old routes", async () => {
  beginSession("A");
  await loadUser("A");
  const { MenuProcessor } = await import("@/router/MenuProcessor");
  const getMenus = MenuProcessor.prototype.getMenuList;
  const gate = deferred<void>();
  let started = false;
  vi.spyOn(MenuProcessor.prototype, "getMenuList").mockImplementationOnce(async function (
    this: InstanceType<typeof MenuProcessor>
  ) {
    const menus = await getMenus.call(this);
    started = true;
    await gate.promise;
    return menus;
  });
  const refreshing = guards.refreshMenuAndRoutes();
  await vi.waitFor(() => expect(started).toBe(true));
  user.resetAllState();
  guards.resetDynamicRoutesSync();
  beginSession("B");
  await loadUser("B");
  await router.push("/B");
  gate.resolve();
  await refreshing;
  expect(router.hasRoute("APage")).toBe(false);
  expect(router.hasRoute("BPage")).toBe(true);
});

it("allows B to initialize while an OAuth-style switch leaves A's initialization pending", async () => {
  beginSession("A");
  const navigation = router.push("/home");
  const oldInfo = await take("/system/user/current/info");
  beginSession("B", true);
  await loadUser("B");
  await router.push("/B");
  expect(router.hasRoute("BPage")).toBe(true);
  succeed(oldInfo, userData("A"));
  await navigation;
  expect(router.hasRoute("APage")).toBe(false);
  expect(guards.getRouteInitFailed()).toBe(false);
  expect(user.info.username).toBe("B");
});

it("migrates a legacy session and removes inactive storage on remember changes", async () => {
  sessionStorage.setItem("access_token", "legacy");
  sessionStorage.setItem("refresh_token", "legacy-refresh");
  const legacyKey = Auth.getSessionKey();
  expect(legacyKey).not.toBe("");
  vi.resetModules();
  const { Auth: reloadedAuth } = await import("./index");
  expect(reloadedAuth.getSessionKey()).toBe(legacyKey);
  reloadedAuth.setTokens("remembered", "remembered-refresh", true);
  expect(sessionStorage.getItem("auth_session")).toBeNull();
  reloadedAuth.setTokens("temporary", "temporary-refresh", false);
  expect(localStorage.getItem("auth_session")).toBeNull();
  expect(localStorage.getItem("access_token")).toBeNull();
});
