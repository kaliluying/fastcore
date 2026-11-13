<!-- 布局内容 -->
<template>
  <div id="app-scroll-main" class="layout-content" :style="containerStyle">
    <div id="app-content-header">
      <!-- 路由信息调试 -->
      <div
        v-if="isOpenRouteInfo === 'true'"
        class="px-2 py-1.5 mb-3 text-sm text-g-500 bg-g-200 border border-[var(--default-border)] rounded-md"
      >
        router meta：{{ route.meta }}
      </div>
    </div>

    <RouterView v-if="isRefresh" v-slot="{ Component, route: router }" :style="contentStyle">
      <Transition :name="actualTransition" mode="out-in">
        <div v-if="Component" class="route-view-shell flex min-h-0 min-w-0 w-full flex-1 flex-col">
          <!-- KeepAlive 必须常驻；缓存取舍由 include/exclude 表达，不能按当前路由 v-if 卸载整个缓存容器。 -->
          <KeepAlive :max="10" :include="keepAliveInclude" :exclude="effectiveKeepAliveExclude">
            <component
              class="fa-page-view min-h-0 min-w-0 w-full flex-1"
              :is="Component"
              :key="routeLeafCacheKey(router)"
            />
          </KeepAlive>
        </div>
      </Transition>
    </RouterView>

    <!-- 返回顶部：宽屏 #app-scroll-main；窄屏文档滚动 target 置空 -->
    <ElBacktop
      :key="backtopTargetKey"
      :target="backtopScrollTarget"
      :right="28"
      :bottom="28"
      class="z-90"
    >
      <FaSvgIcon icon="ri:arrow-up-circle-line" class="text-2xl text-theme" />
    </ElBacktop>
  </div>
</template>
<script setup lang="ts">
/**
 * 布局滚动容器 + 业务路由出口；与 settings.refresh 联动可整体重建 RouterView。
 *
 * 缓存开关数据源：后端菜单 `keep_alive` → MenuProcessor 写入 `meta.keepAlive`；工作栏 tab 随路由写入同一 meta。
 * include / wrapPageWithKeepAlive 均用 `!== false`，与静态路由里显式 `keepAlive: false`、后端布尔字段对齐。
 */
import type { CSSProperties } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { useRoute, type RouteLocationNormalizedLoaded } from "vue-router";
import { useSettingsStore, useWorktabStore } from "@stores";

defineOptions({ name: "FaPageContent" });

/**
 * KeepAlive / RouterView 子节点缓存键。
 * - `meta.remountOnFullPath === true`：整 URL 参与键（依赖 query 初值或须随 query 重建的页）。
 * - 有 `name`：`name` + `params`，同一路由仅 query 变化不换实例。
 * - 无 `name`：`path`（勿用 fullPath，否则仅 query 变化也会反复挂载）。
 */
function routeLeafCacheKey(r: RouteLocationNormalizedLoaded): string {
  if (r.meta.remountOnFullPath === true) {
    return r.fullPath;
  }
  if (r.name != null) {
    return `${String(r.name)}:${JSON.stringify(r.params ?? {})}`;
  }
  return r.path;
}

const route = useRoute();

const isNarrowViewport = useMediaQuery("(max-width: 800px)");
const backtopScrollTarget = computed(() => (isNarrowViewport.value ? "" : "#app-content"));
const backtopTargetKey = computed(() => (isNarrowViewport.value ? "win" : "main"));

const { pageTransition, containerWidth, refresh, showWorkTab } = storeToRefs(useSettingsStore());
const { keepAliveExclude, opened } = storeToRefs(useWorktabStore());

/** 多标签开启时：仅已打开且允许缓存的标签组件名进入 include；关闭多标签时不传 include，避免 opened 过窄误伤缓存。 */
const keepAliveInclude = computed(() => {
  if (!showWorkTab.value) return undefined;
  const names = new Set<string>();
  for (const t of opened.value) {
    if (t.name && t.keepAlive !== false) names.add(String(t.name));
  }
  // 首次导航时工作栏可能尚未完成同步，当前页面仍应进入缓存白名单。
  if (route.name != null && route.meta.keepAlive !== false) {
    names.add(String(route.name));
  }
  return names.size ? Array.from(names) : undefined;
});

/** 保留 `meta.keepAlive === false` 的语义：即使关闭工作栏白名单，也不能缓存当前页面。 */
const effectiveKeepAliveExclude = computed(() => {
  const names = new Set(keepAliveExclude.value);
  if (route.meta.keepAlive === false && route.name != null) {
    names.add(String(route.name));
  }
  return Array.from(names);
});

const isRefresh = shallowRef(true);
const isOpenRouteInfo = import.meta.env.VITE_OPEN_ROUTE_INFO;

/** 浏览器首次进入：关闭路由过渡动画，避免首屏闪动 */
const isFirstLoad = ref(true);

const actualTransition = computed(() => {
  if (isFirstLoad.value) return "";
  return pageTransition.value;
});

const containerStyle = computed((): CSSProperties => ({
  width: "100%",
  minWidth: 0,
  maxWidth: containerWidth.value,
  flex: "1",
  minHeight: "0",
  display: "flex",
  flexDirection: "column",
}));

/** 常规布局下由 `.layout-content` 承担纵向滚动，路由视图填满剩余高度 */
const contentStyle = computed((): CSSProperties => ({
  flex: "1",
  minHeight: "0",
  minWidth: 0,
  width: "100%",
}));

const reload = () => {
  isRefresh.value = false;
  nextTick(() => {
    isRefresh.value = true;
  });
};

watch(refresh, reload, { flush: "post" });

// 组件挂载后标记首次加载完成
onMounted(() => {
  // 延迟一帧，确保首次渲染完成
  nextTick(() => {
    isFirstLoad.value = false;
  });
});
</script>
