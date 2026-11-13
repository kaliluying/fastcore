import type { App } from "vue";
import { setupAuthDirective, type AuthDirective } from "./core/auth";
import { setupRippleDirective, type RippleDirective } from "./business/ripple";
import { hasPerm } from "./permission";

/** 全局注册 directive */
export function initGlobDirectives(app: App<Element>) {
  setupAuthDirective(app); // 权限指令
  setupRippleDirective(app); // 水波纹指令

  // 使 v-hasPerm 在所有组件中都可用
  app.directive("hasPerm", hasPerm);
}

export type { AuthDirective, RippleDirective };
