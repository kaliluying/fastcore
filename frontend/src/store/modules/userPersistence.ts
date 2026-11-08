/** 用户 Store 的持久化配置。动态路由属于当前会话数据，不应跨版本缓存。 */
export const USER_STORE_PERSIST_KEY = "user";

export const USER_STORE_PERSISTED_FIELDS = [
  "language",
  "isLogin",
  "info",
  "searchHistory",
  "prems",
  "rememberMe",
];
