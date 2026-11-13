/**
 * useAppBootstrap —— 应用挂载后初始化编排。
 *
 * 统一调用所有 onMounted 阶段的初始化逻辑（存储检查、主题恢复、版本升级、站点配置、
 * 布局偏好 DOM 副作用）。设置面板已移除，原面板内的启动副作用迁到此处。
 */
import { watch } from "vue";
import { useBreakpoints } from "@vueuse/core";
import { useSettingsStore } from "@stores";
import { MenuTypeEnum } from "@/enums/appEnum";
import AppConfig from "@/config";
import { StorageConfig, toggleTransition } from "@utils";

export function useAppBootstrap() {
  /** 设置面板移除后仍需在启动时应用的 DOM / 布局偏好 */
  const applyLayoutPreferences = () => {
    const settingStore = useSettingsStore();

    watch(
      () => settingStore.colorWeak,
      (val) => {
        document.documentElement.classList.toggle("color-weak", Boolean(val));
      },
      { immediate: true }
    );

    document.documentElement.setAttribute(
      "data-box-mode",
      settingStore.boxBorderMode ? "border-mode" : "shadow-mode"
    );

    const themeColor = settingStore.systemThemeColor as string;
    if (!AppConfig.systemMainColor.includes(themeColor)) {
      settingStore.setElementTheme(AppConfig.systemMainColor[0]!);
    }
  };

  /** 窄屏强制左侧菜单并收起，宽屏恢复（原 settings-panel 逻辑） */
  const startResponsiveMenuLayout = () => {
    const settingStore = useSettingsStore();
    const breakpoints = useBreakpoints({ tablet: 1000 });
    const isMobile = breakpoints.smaller("tablet");

    const getStored = (): MenuTypeEnum | undefined => {
      const stored = localStorage.getItem(StorageConfig.RESPONSIVE_MENU_TYPE_KEY);
      return Object.values(MenuTypeEnum).includes(stored as MenuTypeEnum)
        ? (stored as MenuTypeEnum)
        : undefined;
    };

    let beforeMenuType = getStored();
    let hasChangedMenu = Boolean(beforeMenuType);

    watch(
      isMobile,
      (mobile) => {
        if (mobile) {
          if (!hasChangedMenu && settingStore.menuType !== MenuTypeEnum.LEFT) {
            beforeMenuType = settingStore.menuType;
            localStorage.setItem(StorageConfig.RESPONSIVE_MENU_TYPE_KEY, settingStore.menuType);
            settingStore.switchMenuLayouts(MenuTypeEnum.LEFT);
            hasChangedMenu = true;
          }
          settingStore.setMenuOpen(false);
          return;
        }

        if (hasChangedMenu && beforeMenuType && settingStore.menuType === MenuTypeEnum.LEFT) {
          settingStore.switchMenuLayouts(beforeMenuType);
          localStorage.removeItem(StorageConfig.RESPONSIVE_MENU_TYPE_KEY);
          hasChangedMenu = false;
        }
        settingStore.setMenuOpen(true);
      },
      { immediate: true }
    );
  };

  const bootstrap = () => {
    toggleTransition(false);
    applyLayoutPreferences();
    startResponsiveMenuLayout();
  };

  return { bootstrap };
}
