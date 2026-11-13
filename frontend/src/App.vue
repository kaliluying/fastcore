<template>
  <ElConfigProvider
    :size="size"
    :locale="locale"
    :z-index="3000"
    :card="{
      shadow: 'never',
    }"
  >
    <div class="wh-full">
      <RouterView></RouterView>
    </div>
  </ElConfigProvider>
</template>

<script setup lang="ts">
import { computed, onBeforeMount, onMounted } from "vue";
import { useAppStore } from "./store";
import { ComponentSize } from "./enums/settings/layout.enum";
import { toggleTransition } from "./utils/ui";
import { initializeTheme } from "./hooks/core/useTheme";
import { useAppBootstrap } from "@/hooks/core/useAppBootstrap";
import en from "element-plus/es/locale/lang/en";
import zhCn from "element-plus/es/locale/lang/zh-cn";

const appStore = useAppStore();

const size = computed(() => appStore.size as ComponentSize);

// 根据语言设置返回对应的语言包
const locale = computed(() => {
  return appStore.language === "en" ? en : zhCn;
});

// 水印文字默认使用当前主题色（半透明），随主题色设置变化

/**
 * 应用根组件生命周期：
 *
 * onBeforeMount
 *   1. toggleTransition(true)  —— 临时禁用页面过渡，避免主题切换时的闪烁
 *   2. initializeTheme()       —— 加载主题配色(CSS 变量)、暗色模式 class、auto 监听
 *
 * onMounted
 *   1. bootstrap()             —— 过渡恢复 → 站点配置
 */
onBeforeMount(() => {
  toggleTransition(true);
  initializeTheme();
});

const { bootstrap } = useAppBootstrap();

onMounted(() => {
  bootstrap();
});
</script>
