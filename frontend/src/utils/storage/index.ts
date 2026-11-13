/**
 * LocalStorage 配置与键名常量。
 */

/** 存储配置与常用键名常量。 */
export class StorageConfig {
  /** 当前应用版本 */
  static readonly CURRENT_VERSION = __APP_VERSION__;

  /** 应用名称 */
  static readonly appName = __APP_NAME__;

  /** 主题键名（index.html中使用了，如果修改，需要同步修改） */
  static readonly THEME_KEY = "sys-theme";

  /** 上次登录用户ID键名（用于判断是否为同一用户登录） */
  static readonly LAST_USER_ID_KEY = "sys-last-user-id";

  /** 响应式布局切换时暂存桌面端菜单类型 */
  static readonly RESPONSIVE_MENU_TYPE_KEY = "sys-responsive-menu-type";
}
