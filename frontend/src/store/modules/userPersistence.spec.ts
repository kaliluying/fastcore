import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import piniaPluginPersistedstate from "pinia-plugin-persistedstate";
import { createApp } from "vue";

import { USER_STORE_PERSISTED_FIELDS, USER_STORE_PERSIST_KEY } from "./userPersistence";
import { useUserStore } from "./user.store";

describe("用户 Store 持久化", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("不再持久化动态路由状态", () => {
    expect(USER_STORE_PERSISTED_FIELDS).not.toContain("routeList");
    expect(USER_STORE_PERSISTED_FIELDS).not.toContain("hasGetRoute");
    expect(USER_STORE_PERSISTED_FIELDS).not.toContain("isLock");
    expect(USER_STORE_PERSISTED_FIELDS).not.toContain("lockPassword");
  });

  it("Pinia hydration 时重置动态路由且只持久化白名单字段", () => {
    localStorage.setItem(
      USER_STORE_PERSIST_KEY,
      JSON.stringify({
        isLogin: true,
        info: { id: 1 },
      })
    );

    const pinia = createPinia();
    pinia.use(piniaPluginPersistedstate);
    createApp({}).use(pinia);
    setActivePinia(pinia);
    const userStore = useUserStore(pinia);

    expect(userStore.routeList).toEqual([]);
    expect(userStore.hasGetRoute).toBe(false);

    userStore.setRoute([{ name: "新菜单" } as any]);
    expect(userStore.routeList.length).toBe(1);

    const persisted = JSON.parse(localStorage.getItem(USER_STORE_PERSIST_KEY) || "{}");
    expect(persisted).not.toHaveProperty("routeList");
    expect(persisted).not.toHaveProperty("hasGetRoute");
  });
});
