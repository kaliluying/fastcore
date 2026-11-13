<template>
  <div class="home-workspace">
    <Banner
      :health-state="healthState"
      :health-description="healthDescription"
      :health-loading="healthLoading"
      :user-name="userStore.basicInfo.name || userStore.basicInfo.username || ''"
      @refresh="loadHealthStatus"
    />
    <section v-if="featuredLinks.length" class="knowledge-launch" aria-labelledby="launch-title">
      <h2 id="launch-title" class="workspace-section-title">知识工作</h2>
      <nav class="knowledge-launch__resources" aria-label="知识工作入口">
        <RouterLink v-if="chatLink" :to="chatLink.path" class="knowledge-launch__action">
          <span class="knowledge-launch__icon" aria-hidden="true">
            <FaSvgIcon :icon="chatLink.icon" />
          </span>
          <strong>{{ formatMenuTitle(chatLink.title) }}</strong>
          <p>围绕团队资料提问，获取带有来源的回答。</p>
          <span class="knowledge-launch__link">
            开始 AI 对话 <FaSvgIcon icon="ri:arrow-right-line" aria-hidden="true" />
          </span>
        </RouterLink>
        <RouterLink v-for="link in knowledgeLinks" :key="link.path" :to="link.path">
          <span class="knowledge-launch__icon" aria-hidden="true">
            <FaSvgIcon :icon="link.icon" />
          </span>
          <strong>{{ formatMenuTitle(link.title) }}</strong>
          <p>
            {{
              link.path === "/ai/knowledge"
                ? "组织团队知识，管理资料与访问范围。"
                : "上传文档，跟踪解析与索引处理进度。"
            }}
          </p>
          <span class="knowledge-launch__link">
            {{ link.path === "/ai/knowledge" ? "管理知识库" : "管理文档" }}
            <FaSvgIcon icon="ri:arrow-right-line" aria-hidden="true" />
          </span>
        </RouterLink>
      </nav>
    </section>
    <div
      class="home-content"
      :class="{ 'home-content--single': !canViewLoginTrend || !managementLinks.length }"
    >
      <LoginTrend v-if="canViewLoginTrend" />

      <section
        v-if="managementLinks.length"
        class="quick-links"
        aria-labelledby="quick-links-title"
      >
        <div class="quick-links__header">
          <h2 id="quick-links-title">管理与配置</h2>
          <span>快捷入口</span>
        </div>
        <nav aria-label="快捷入口">
          <RouterLink v-for="link in managementLinks" :key="link.path" :to="link.path">
            <span class="quick-links__icon" aria-hidden="true">
              <FaSvgIcon :icon="link.icon" />
            </span>
            <span>{{ formatMenuTitle(link.title) }}</span>
            <FaSvgIcon icon="ri:arrow-right-s-line" class="quick-links__arrow" aria-hidden="true" />
          </RouterLink>
        </nav>
      </section>

      <p v-if="!canViewLoginTrend && !quickLinks.length" class="home-empty">
        暂无可用入口，请联系管理员分配访问权限。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import HealthAPI, { type HealthReadiness } from "@/api/module_common/health";
import type { AppRouteRecord } from "@/types/router";
import { useMenuStore, useUserStore } from "@stores";
import { hasPermissionCode } from "@/utils/auth/permission";
import { formatMenuTitle, HttpError } from "@utils";
import Banner from "./modules/banner.vue";
import LoginTrend from "./modules/login-trend.vue";

defineOptions({ name: "Home", inheritAttrs: false });

type HealthState = "loading" | "healthy" | "degraded" | "unavailable";

const HEALTH_REFRESH_INTERVAL = 30_000;
const menuStore = useMenuStore();
const userStore = useUserStore();
const canViewLoginTrend = computed(
  () =>
    (userStore.basicInfo.modules?.includes("dashboard") ?? true) &&
    hasPermissionCode("module_system:login_log:query", {
      is_superuser: userStore.basicInfo.is_superuser,
      permissions: userStore.getPerms,
    })
);
const quickLinks = computed(() => {
  const links: { path: string; title: string; icon: string }[] = [];
  const visit = (routes: AppRouteRecord[]) => {
    for (const route of routes) {
      if (route.meta?.hidden || route.meta?.isHide) continue;
      if (route.children?.length) {
        visit(route.children);
        continue;
      }
      if (
        route.path &&
        route.path !== "/home" &&
        route.path.startsWith("/") &&
        !route.path.startsWith("//") &&
        !route.path.includes(":") &&
        route.component &&
        route.meta?.title &&
        !route.meta?.link &&
        !route.meta?.isIframe
      ) {
        links.push({
          path: route.path,
          title: route.meta.title,
          icon: route.meta.icon || "ri:apps-line",
        });
      }
    }
  };
  visit(menuStore.menuList);
  return links;
});
const featuredLinks = computed(() =>
  quickLinks.value.filter((link) =>
    ["/ai/chat", "/ai/knowledge", "/ai/document"].includes(link.path)
  )
);
const chatLink = computed(() => featuredLinks.value.find((link) => link.path === "/ai/chat"));
const knowledgeLinks = computed(() =>
  featuredLinks.value.filter((link) => link.path !== "/ai/chat")
);
const managementLinks = computed(() =>
  quickLinks.value.filter((link) => !featuredLinks.value.includes(link))
);
const healthState = ref<HealthState>("loading");
const healthData = ref<HealthReadiness | null>(null);
const healthLoading = ref(false);
const hasLoaded = ref(false);
const healthDescription = computed(() => {
  if (healthState.value === "unavailable") return "暂时无法检查系统状态，请稍后重试";
  if (!healthData.value) return "";

  const failedDependencies = Object.entries(healthData.value.dependencies)
    .filter(([, dependency]) => dependency.enabled && dependency.status !== 1)
    .map(([name]) => (name === "database" ? "数据库" : name === "redis" ? "Redis" : name));
  if (failedDependencies.length) return `${failedDependencies.join("、")}连接异常`;
  return "基础服务需要检查";
});

function isHealthReadiness(value: unknown): value is HealthReadiness {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  if (
    (candidate.status !== 0 && candidate.status !== 1) ||
    typeof candidate.disk_usage !== "number" ||
    !candidate.dependencies ||
    typeof candidate.dependencies !== "object"
  )
    return false;

  return ["database", "redis"].every((name) => {
    const dependency = (candidate.dependencies as Record<string, unknown>)[name];
    return (
      dependency &&
      typeof dependency === "object" &&
      typeof (dependency as Record<string, unknown>).enabled === "boolean" &&
      ((dependency as Record<string, unknown>).status === 0 ||
        (dependency as Record<string, unknown>).status === 1)
    );
  });
}

function extractReadinessPayload(error: unknown): HealthReadiness | null {
  if (!(error instanceof HttpError) || !error.data || typeof error.data !== "object") return null;
  const payload = (error.data as Record<string, unknown>).data;
  return isHealthReadiness(payload) ? payload : null;
}

async function loadHealthStatus(): Promise<void> {
  if (healthLoading.value) return;

  healthLoading.value = true;
  try {
    const response = await HealthAPI.getReadiness();
    const readiness = response.data.data;
    healthData.value = readiness;
    healthState.value = readiness.status === 1 ? "healthy" : "degraded";
  } catch (error: unknown) {
    const readiness = extractReadinessPayload(error);
    healthData.value = readiness;
    healthState.value = readiness ? "degraded" : "unavailable";
  } finally {
    healthLoading.value = false;
    hasLoaded.value = true;
  }
}

let healthRefreshTimer: number | undefined;

const startHealthTimer = () => {
  if (healthRefreshTimer === undefined) {
    healthRefreshTimer = window.setInterval(() => void loadHealthStatus(), HEALTH_REFRESH_INTERVAL);
  }
};

const stopHealthTimer = () => {
  if (healthRefreshTimer !== undefined) {
    window.clearInterval(healthRefreshTimer);
    healthRefreshTimer = undefined;
  }
};

onMounted(() => {
  void loadHealthStatus();
  startHealthTimer();
});

onActivated(() => {
  if (hasLoaded.value) {
    void loadHealthStatus();
    startHealthTimer();
  }
});

onDeactivated(stopHealthTimer);
onUnmounted(stopHealthTimer);
</script>

<style scoped lang="scss">
.home-workspace {
  width: 100%;
  min-width: 0;
  max-width: 1840px;
  margin: 0 auto;
}

.workspace-section-title {
  margin: 0 0 14px;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--fa-color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

/* 知识工作 Bento 区域 */
.knowledge-launch {
  margin-bottom: 24px;
}

.knowledge-launch__resources {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 16px;
  min-width: 0;
}

.knowledge-launch__resources a {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  min-height: 200px;
  padding: 22px;
  overflow: hidden;
  color: var(--fa-color-text);
  text-decoration: none;
  background-color: var(--fa-color-surface);
  background-image: var(--fa-bento-card-bg);
  border: 1px solid var(--fa-glass-border, var(--fa-color-border));
  border-radius: var(--fa-radius-panel);
  box-shadow: var(--fa-shadow-bento, var(--fa-soft-shadow));
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);

  &::before {
    position: absolute;
    inset: 0;
    padding: 1px;
    pointer-events: none;
    content: "";
    background: linear-gradient(135deg, var(--fa-glass-border-highlight) 0%, transparent 60%);
    border-radius: inherit;
    -webkit-mask:
      linear-gradient(#fff 0 0) content-box,
      linear-gradient(#fff 0 0);
    mask:
      linear-gradient(#fff 0 0) content-box,
      linear-gradient(#fff 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
  }
}

.knowledge-launch__resources a:hover {
  background-color: var(--fa-color-surface);
  background-image: none;
  border-color: color-mix(in srgb, var(--theme-color) 40%, var(--fa-glass-border));
  box-shadow: var(--fa-shadow-bento-hover);
  transform: translateY(-3px);
}

.knowledge-launch__resources a.knowledge-launch__action {
  background-color: color-mix(in srgb, var(--theme-color) 7%, var(--fa-color-surface));
  background-image: linear-gradient(
    145deg,
    color-mix(in srgb, var(--theme-color) 12%, var(--fa-glass-bg)),
    var(--fa-glass-bg)
  );
  border-color: color-mix(in srgb, var(--theme-color) 35%, var(--fa-glass-border));

  &::after {
    position: absolute;
    top: 0;
    right: 0;
    left: 0;
    height: 3px;
    content: "";
    background: linear-gradient(90deg, var(--theme-color), transparent);
  }
}

.knowledge-launch__icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  font-size: 22px;
  color: var(--theme-color);
  background: color-mix(in srgb, var(--theme-color) 12%, transparent);
  border-radius: 12px;
  box-shadow: 0 4px 12px -2px color-mix(in srgb, var(--theme-color) 25%, transparent);
}

.knowledge-launch__resources strong {
  margin-top: 14px;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.4;
  letter-spacing: -0.01em;
}

.knowledge-launch__resources p {
  margin: 6px 0 16px;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--fa-color-text-muted);
}

.knowledge-launch__link {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  margin-top: auto;
  font-size: 13px;
  font-weight: 700;
  color: color-mix(in srgb, var(--theme-color) 70%, var(--fa-color-text));
  transition: transform 0.2s ease;
}

.knowledge-launch__resources a:hover .knowledge-launch__link {
  transform: translateX(3px);
}

.knowledge-launch a:focus-visible {
  outline: 2px solid var(--theme-color);
  outline-offset: 4px;
}

/* 主内容布局：登录趋势与快捷入口 */
.home-content {
  display: grid;
  grid-template-columns: minmax(0, 1.9fr) minmax(320px, 1fr);
  gap: 20px;
  align-items: start;
}

.home-content--single {
  grid-template-columns: minmax(0, 1fr);
}

/* 快捷入口卡片 */
.quick-links {
  position: relative;
  min-width: 0;
  padding: 22px;
  background: var(--fa-glass-bg, var(--fa-color-surface));
  border: 1px solid var(--fa-glass-border, var(--fa-color-border));
  border-radius: var(--fa-radius-panel);
  box-shadow: var(--fa-shadow-bento, var(--fa-soft-shadow));
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);
}

.quick-links__header {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}

.quick-links h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--fa-color-text);
  letter-spacing: -0.01em;
}

.quick-links__header > span {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--fa-color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.quick-links nav {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
}

.quick-links a {
  display: flex;
  gap: 12px;
  align-items: center;
  min-width: 0;
  min-height: 46px;
  padding: 8px 12px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--fa-color-text);
  text-decoration: none;
  background: var(--fa-glass-bg-inset, var(--fa-color-surface-raised));
  border: 1px solid transparent;
  border-radius: var(--fa-radius-control);
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.quick-links a:hover {
  color: var(--theme-color);
  background: var(--fa-glass-bg-hover, var(--fa-color-surface));
  border-color: color-mix(in srgb, var(--theme-color) 35%, var(--fa-glass-border));
  box-shadow: 0 4px 12px -2px rgb(15 23 42 / 4%);
  transform: translateX(2px);
}

.quick-links a:focus-visible {
  outline: 2px solid var(--theme-color);
  outline-offset: 2px;
}

.quick-links__icon {
  display: grid;
  flex: 0 0 32px;
  place-items: center;
  width: 32px;
  height: 32px;
  font-size: 16px;
  color: var(--theme-color);
  background: color-mix(in srgb, var(--theme-color) 10%, transparent);
  border-radius: 8px;
}

.quick-links__arrow {
  margin-left: auto;
  font-size: 16px;
  color: var(--fa-color-text-muted);
  transition: transform 0.2s ease;
}

.quick-links a:hover .quick-links__arrow {
  color: var(--theme-color);
  transform: translateX(2px);
}

.home-empty {
  padding: 22px;
  margin: 0;
  font-size: 13.5px;
  color: var(--fa-color-text-muted);
  background: var(--fa-glass-bg, var(--fa-color-surface));
  border: 1px solid var(--fa-glass-border, var(--fa-color-border));
  border-radius: var(--fa-radius-panel);
  box-shadow: var(--fa-shadow-bento);
}

@media (width <= 1100px) {
  .home-content {
    grid-template-columns: minmax(0, 1fr);
  }

  .quick-links {
    order: -1;
  }

  .quick-links nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (width <= 520px) {
  .knowledge-launch {
    margin-bottom: 20px;
  }

  .knowledge-launch__resources {
    gap: 12px;
  }

  .knowledge-launch__resources a {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr);
    gap: 4px 14px;
    min-height: 0;
    padding: 16px;
  }

  .knowledge-launch__icon {
    grid-row: 1 / 4;
  }

  .knowledge-launch__resources strong {
    margin-top: 0;
    font-size: 15px;
  }

  .knowledge-launch__resources p {
    margin: 0 0 8px;
    font-size: 12.5px;
  }

  .quick-links {
    padding: 16px;
  }

  .quick-links nav {
    grid-template-columns: minmax(0, 1fr);
  }

  .quick-links a {
    min-height: 48px;
    padding: 8px 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-workspace a,
  .quick-links__arrow,
  .knowledge-launch__link {
    transform: none !important;
    transition: none !important;
  }
}
</style>
