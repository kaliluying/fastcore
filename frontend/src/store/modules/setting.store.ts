/**
 * 全局界面设置：布局、主题、页签、水印、工具栏显隐等；部分字段持久化（useStorage / persist）。
 * `reload` 翻转 `refresh` 布尔值，触发 `FaPageContent` 重建 RouterView（与 useCommon.refresh 一致）。
 */
import { defineStore } from "pinia";
import { ref, computed, watch } from "vue";
import { MenuThemeType } from "@/types/store";
import AppConfig from "@/config";
import { SystemThemeEnum, MenuThemeEnum, MenuTypeEnum, ContainerWidthEnum } from "@/enums/appEnum";
import { SETTING_DEFAULT_CONFIG } from "@/config/setting";
import {
  setElementThemeColor,
  applyTheme,
  generateThemeColors,
  toggleDarkMode,
  toggleSidebarColor,
} from "@/utils/ui/colors";
import { StorageConfig } from "@/utils/storage";
import { SETTINGS_KEYS } from "@/constants";
import { useStorage } from "@vueuse/core";
import { defaultSettings } from "@/config/setting";
import { SidebarColor, ThemeMode } from "@/enums/settings/theme.enum";
import type { LayoutMode } from "@/enums/settings/layout.enum";
import type { Ref } from "vue";

export const useSettingsStore = defineStore(
  "settingStore",
  () => {
    // 菜单
    const menuType = ref(SETTING_DEFAULT_CONFIG.menuType);
    const menuOpenWidth = ref(SETTING_DEFAULT_CONFIG.menuOpenWidth);
    const menuOpen = ref(SETTING_DEFAULT_CONFIG.menuOpen);
    const dualMenuShowText = ref(SETTING_DEFAULT_CONFIG.dualMenuShowText);

    // 主题
    const systemThemeType = ref(SETTING_DEFAULT_CONFIG.systemThemeType);
    const systemThemeMode = ref(SETTING_DEFAULT_CONFIG.systemThemeMode);
    const menuThemeType = ref(SETTING_DEFAULT_CONFIG.menuThemeType);
    const systemThemeColor = ref(SETTING_DEFAULT_CONFIG.systemThemeColor);

    // 显示
    const showMenuButton = ref(SETTING_DEFAULT_CONFIG.showMenuButton);
    const showRefreshButton = ref(SETTING_DEFAULT_CONFIG.showRefreshButton);
    const showCrumbs = ref(SETTING_DEFAULT_CONFIG.showCrumbs);
    const showWorkTab = ref(SETTING_DEFAULT_CONFIG.showWorkTab);
    const showLanguage = ref(SETTING_DEFAULT_CONFIG.showLanguage);
    const showNprogress = ref(SETTING_DEFAULT_CONFIG.showNprogress);
    const watermarkVisible = ref(SETTING_DEFAULT_CONFIG.watermarkVisible);

    // 功能
    const autoClose = ref(SETTING_DEFAULT_CONFIG.autoClose);
    const uniqueOpened = ref(SETTING_DEFAULT_CONFIG.uniqueOpened);
    const colorWeak = ref(SETTING_DEFAULT_CONFIG.colorWeak);
    /** 供布局监听：`reload()` 取反以触发主内容区整页刷新 */
    const refresh = ref(SETTING_DEFAULT_CONFIG.refresh);

    // 样式
    const boxBorderMode = ref(SETTING_DEFAULT_CONFIG.boxBorderMode);
    const pageTransition = ref(SETTING_DEFAULT_CONFIG.pageTransition);
    const tabStyle = ref(SETTING_DEFAULT_CONFIG.tabStyle);
    const customRadius = ref(SETTING_DEFAULT_CONFIG.customRadius);
    const containerWidth = ref(SETTING_DEFAULT_CONFIG.containerWidth);

    // 持久化（web-style：useStorage）
    const showTagsView = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_TAGS_VIEW,
      defaultSettings.showTagsView
    );
    const showAppLogo = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_APP_LOGO,
      defaultSettings.showAppLogo
    );
    const showWatermark = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_WATERMARK,
      defaultSettings.showWatermark
    );
    const showGuide = useStorage<boolean>(SETTINGS_KEYS.SHOW_GUIDE, defaultSettings.showGuide);

    // 桌面端工具设置 - 持久化
    const showMenuSearch = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_MENU_SEARCH,
      defaultSettings.showMenuSearch
    );
    const showFullscreen = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_FULLSCREEN,
      defaultSettings.showFullscreen
    );
    const showSizeSelect = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_SIZE_SELECT,
      defaultSettings.showSizeSelect
    );
    const showLangSelect = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_LANG_SELECT,
      defaultSettings.showLangSelect
    );
    const showNotification = useStorage<boolean>(
      SETTINGS_KEYS.SHOW_NOTIFICATION,
      defaultSettings.showNotification
    );

    // 布局和主题设置 - 持久化
    const sidebarColorScheme = useStorage<string>(
      SETTINGS_KEYS.SIDEBAR_COLOR_SCHEME,
      defaultSettings.sidebarColorScheme
    );
    const layout = useStorage<LayoutMode>(
      SETTINGS_KEYS.LAYOUT,
      defaultSettings.layout as LayoutMode
    );

    // 系统设置 - 持久化
    const grayMode = useStorage<boolean>(SETTINGS_KEYS.GRAY_MODE, defaultSettings.grayMode);
    const pageSwitchingAnimation = useStorage<string>(
      SETTINGS_KEYS.PAGE_SWITCHING_ANIMATION,
      defaultSettings.pageSwitchingAnimation
    );

    const getMenuTheme = computed((): MenuThemeType => {
      const list = AppConfig.themeList.filter((item) => item.theme === menuThemeType.value);
      if (isDark.value) {
        return AppConfig.darkMenuStyles[0]!;
      } else {
        return list[0]!;
      }
    });

    const isDark = computed((): boolean => {
      return systemThemeType.value === SystemThemeEnum.DARK;
    });

    const getMenuOpenWidth = computed((): string => {
      return (menuOpenWidth.value ?? SETTING_DEFAULT_CONFIG.menuOpenWidth) + "px";
    });

    const getCustomRadius = computed((): string => {
      return (customRadius.value ?? SETTING_DEFAULT_CONFIG.customRadius) + "rem";
    });

    const settingsMap = {
      showTagsView,
      showAppLogo,
      showWatermark,
      showGuide,
      showMenuSearch,
      showFullscreen,
      showSizeSelect,
      showLangSelect,
      showNotification,
      sidebarColorScheme,
      layout,
      grayMode,
    } as const;

    watch(
      [systemThemeType, systemThemeColor],
      ([newTheme, newThemeColor]) => {
        try {
          toggleDarkMode(newTheme === SystemThemeEnum.DARK);
          const colors = generateThemeColors(
            newThemeColor!,
            newTheme === SystemThemeEnum.DARK ? ThemeMode.DARK : ThemeMode.LIGHT
          );
          applyTheme(colors);
        } catch (error) {
          console.error("[SettingStore] 主题初始化失败:", error);
        }
      },
      { immediate: true }
    );

    watch(
      [sidebarColorScheme],
      ([newSidebarColorScheme]) => {
        toggleSidebarColor(newSidebarColorScheme === SidebarColor.CLASSIC_BLUE);
      },
      { immediate: true }
    );

    watch(
      grayMode,
      (v) => {
        document.documentElement.style.filter = v ? "grayscale(100%)" : "";
      },
      { immediate: true }
    );

    const switchMenuLayouts = (type: MenuTypeEnum) => {
      menuType.value = type;
    };

    const setMenuOpenWidth = (width: number) => {
      menuOpenWidth.value = width;
    };

    const setGlopTheme = (theme: SystemThemeEnum, themeMode: SystemThemeEnum) => {
      systemThemeType.value = theme;
      systemThemeMode.value = themeMode;
      localStorage.setItem(StorageConfig.THEME_KEY, theme);
    };

    const switchMenuStyles = (theme: MenuThemeEnum) => {
      menuThemeType.value = theme;
    };

    const setElementTheme = (theme: string) => {
      systemThemeColor.value = theme;
      setElementThemeColor(theme, isDark.value);
    };

    const setBorderMode = () => {
      boxBorderMode.value = !boxBorderMode.value;
    };

    const setContainerWidth = (width: ContainerWidthEnum) => {
      containerWidth.value = width;
    };

    const setUniqueOpened = () => {
      uniqueOpened.value = !uniqueOpened.value;
    };

    const setButton = () => {
      showMenuButton.value = !showMenuButton.value;
    };

    const setAutoClose = () => {
      autoClose.value = !autoClose.value;
    };

    const setShowRefreshButton = () => {
      showRefreshButton.value = !showRefreshButton.value;
    };

    const setCrumbs = () => {
      showCrumbs.value = !showCrumbs.value;
    };

    const setWorkTab = (show: boolean) => {
      showWorkTab.value = show;
    };

    const setLanguage = () => {
      showLanguage.value = !showLanguage.value;
    };

    const setNprogress = () => {
      showNprogress.value = !showNprogress.value;
    };

    const setColorWeak = () => {
      colorWeak.value = !colorWeak.value;
    };

    const setPageTransition = (transition: string) => {
      pageTransition.value = transition;
    };

    const setTabStyle = (style: string) => {
      tabStyle.value = style;
    };

    const setMenuOpen = (open: boolean) => {
      menuOpen.value = open;
    };

    /** 切换 `refresh`，驱动 `layouts/art-page-content` 内 `v-if="isRefresh"` 重建视图 */
    const reload = () => {
      refresh.value = !refresh.value;
    };

    const setWatermarkVisible = (visible: boolean) => {
      watermarkVisible.value = visible;
    };

    const setCustomRadius = (radius: string) => {
      const value = Number(radius);
      const normalizedRadius = Number.isFinite(value) && value > 0.5 ? "0.5" : radius;
      customRadius.value = normalizedRadius;
      document.documentElement.style.setProperty("--custom-radius", `${normalizedRadius}rem`);
    };

    const setDualMenuShowText = (show: boolean) => {
      dualMenuShowText.value = show;
    };

    function updateSetting<K extends keyof typeof settingsMap>(
      key: K,
      value: boolean | string
    ): void {
      const setting = settingsMap[key];
      if (setting) {
        (setting as Ref<any>).value = value;
      }
    }

    function updateSidebarColorScheme(newScheme: string): void {
      sidebarColorScheme.value = newScheme;
    }

    function updateLayout(newLayout: LayoutMode): void {
      layout.value = newLayout;
    }

    function updateGrayMode(newValue: boolean): void {
      grayMode.value = newValue;
    }

    function updatePageSwitchingAnimation(newValue: string): void {
      pageSwitchingAnimation.value = newValue;
    }

    function resetSettings(): void {
      // 界面显示设置
      showTagsView.value = defaultSettings.showTagsView;
      showAppLogo.value = defaultSettings.showAppLogo;
      showWatermark.value = defaultSettings.showWatermark;
      showGuide.value = defaultSettings.showGuide;

      // 桌面端工具设置
      showMenuSearch.value = defaultSettings.showMenuSearch;
      showFullscreen.value = defaultSettings.showFullscreen;
      showSizeSelect.value = defaultSettings.showSizeSelect;
      showLangSelect.value = defaultSettings.showLangSelect;
      showNotification.value = defaultSettings.showNotification;

      // 布局和主题设置
      sidebarColorScheme.value = defaultSettings.sidebarColorScheme;
      layout.value = defaultSettings.layout as LayoutMode;
      setGlopTheme(defaultSettings.systemThemeType, defaultSettings.systemThemeMode);
      setElementTheme(defaultSettings.systemThemeColor!);

      // 系统设置
      grayMode.value = defaultSettings.grayMode;
      pageSwitchingAnimation.value = defaultSettings.pageSwitchingAnimation;
    }

    // ==============================================
    // 返回所有状态和方法
    // ==============================================

    return {
      // 从 setting.ts 来的状态
      menuType,
      menuOpenWidth,
      menuOpen,
      dualMenuShowText,
      systemThemeType,
      systemThemeMode,
      menuThemeType,
      systemThemeColor,
      showMenuButton,
      showRefreshButton,
      showCrumbs,
      showWorkTab,
      showLanguage,
      showNprogress,
      watermarkVisible,
      autoClose,
      uniqueOpened,
      colorWeak,
      refresh,
      boxBorderMode,
      pageTransition,
      tabStyle,
      customRadius,
      containerWidth,

      // 从 settings.store.ts 来的状态
      showTagsView,
      showAppLogo,
      showWatermark,
      showGuide,
      showMenuSearch,
      showFullscreen,
      showSizeSelect,
      showLangSelect,
      showNotification,
      sidebarColorScheme,
      layout,
      grayMode,
      pageSwitchingAnimation,

      // 计算属性
      getMenuTheme,
      isDark,
      getMenuOpenWidth,
      getCustomRadius,

      // 从 setting.ts 来的方法
      switchMenuLayouts,
      setMenuOpenWidth,
      setGlopTheme,
      switchMenuStyles,
      setElementTheme,
      setBorderMode,
      setContainerWidth,
      setUniqueOpened,
      setButton,
      setAutoClose,
      setShowRefreshButton,
      setCrumbs,
      setWorkTab,
      setLanguage,
      setNprogress,
      setColorWeak,
      setPageTransition,
      setTabStyle,
      setMenuOpen,
      reload,
      setWatermarkVisible,
      setCustomRadius,
      setDualMenuShowText,

      // 从 settings.store.ts 来的方法
      updateSetting,
      updateSidebarColorScheme,
      updateLayout,
      updateGrayMode,
      updatePageSwitchingAnimation,
      resetSettings,
    };
  },
  {
    persist: {
      key: "setting",
      storage: localStorage,
    },
  }
);
