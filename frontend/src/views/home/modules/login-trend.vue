<template>
  <ElCard shadow="never" class="login-trend-card" :aria-busy="loading">
    <template #header>
      <div class="section-heading">
        <div>
          <h2>登录活动</h2>
          <p>近 7 日</p>
        </div>
        <div class="section-actions">
          <ElTooltip content="刷新登录趋势" placement="top">
            <ElButton plain circle :loading="loading" aria-label="刷新登录趋势" @click="loadTrend">
              <FaSvgIcon icon="ri:refresh-line" />
            </ElButton>
          </ElTooltip>
        </div>
      </div>
    </template>

    <div class="trend-content">
      <div v-if="loading && !hasLoaded" class="trend-state" role="status">
        <FaSvgIcon icon="ri:loader-4-line" class="trend-state__icon trend-state__icon--spin" />
        <span>正在读取登录日志…</span>
      </div>

      <div v-else-if="trendError" class="trend-state trend-state--error" role="alert">
        <FaSvgIcon icon="ri:error-warning-line" class="trend-state__icon" />
        <span>{{ trendError }}</span>
      </div>

      <template v-else>
        <div class="trend-summary" aria-label="近七日登录摘要">
          <div class="trend-summary__item">
            <span>成功登录</span>
            <strong>{{ totalLogins }}</strong>
          </div>
          <div class="trend-summary__item">
            <span>新增账号</span>
            <strong>{{ totalNewUsers }}</strong>
          </div>
        </div>

        <div
          v-if="hasActivity && !reducedMotion"
          ref="trendChart"
          class="trend-chart"
          aria-label="近七日登录折线图"
        >
          <FaLineChart
            :data="chartSeries"
            :x-axis-data="chartLabels"
            :colors="chartColors"
            height="100%"
            :show-area-color="true"
            :show-legend="true"
            legend-position="top"
            :show-axis-line="false"
            :show-split-line="true"
            :smooth="false"
          />
        </div>
        <div v-else-if="!hasActivity" class="trend-state" role="status">
          <FaSvgIcon icon="ri:bar-chart-2-line" class="trend-state__icon" />
          <span>近 7 日暂无成功登录记录</span>
        </div>
        <details v-if="trendItems.length" class="trend-details" :open="reducedMotion">
          <summary>查看每日明细</summary>
          <div class="trend-details__table">
            <table>
              <caption class="sr-only">
                近七日登录活动
              </caption>
              <thead>
                <tr>
                  <th scope="col">日期</th>
                  <th scope="col">成功登录</th>
                  <th scope="col">独立用户</th>
                  <th scope="col">新增账号</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in trendItems" :key="item.day">
                  <th scope="row">{{ item.day.slice(5) }}</th>
                  <td>{{ item.logins }}</td>
                  <td>{{ item.unique_users }}</td>
                  <td>{{ item.new_users }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </template>
    </div>
  </ElCard>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { usePreferredReducedMotion, useResizeObserver } from "@vueuse/core";
import { useSettingsStore } from "@stores";
import { ApiStatus, getCssVar, HttpError } from "@utils";
import DashboardAPI, { type LoginTrendItem } from "@/api/module_common/dashboard";
import FaLineChart from "@/components/charts/fa-line-chart/index.vue";
import { echarts } from "@/plugins/echarts";
import type { LineDataItem } from "@/types/component/chart";

defineOptions({ name: "LoginTrend" });

const REFRESH_INTERVAL = 60_000;
const settingsStore = useSettingsStore();
const trendItems = ref<LoginTrendItem[]>([]);
const loading = ref(false);
const hasLoaded = ref(false);
const trendError = ref("");
const isAuthBlocked = ref(false);
const preferredMotion = usePreferredReducedMotion();
const reducedMotion = computed(() => preferredMotion.value === "reduce");
const trendChart = ref<HTMLElement>();
useResizeObserver(trendChart, () => {
  const chartElement = trendChart.value?.firstElementChild;
  if (chartElement instanceof HTMLElement) echarts.getInstanceByDom(chartElement)?.resize();
});

const totalLogins = computed(() =>
  trendItems.value.reduce((total, item) => total + item.logins, 0)
);
const totalNewUsers = computed(() =>
  trendItems.value.reduce((total, item) => total + item.new_users, 0)
);
const hasActivity = computed(() => trendItems.value.some((item) => item.logins > 0));
const chartLabels = computed(() => trendItems.value.map((item) => item.day.slice(5)));
const chartSeries = computed<LineDataItem[]>(() => [
  {
    name: "成功登录",
    data: trendItems.value.map((item) => item.logins),
    showAreaColor: true,
  },
  {
    name: "独立用户",
    data: trendItems.value.map((item) => item.unique_users),
    lineWidth: 2,
  },
]);
const chartColors = computed(() => [
  settingsStore.systemThemeColor || getCssVar("--el-color-primary"),
  getCssVar("--el-color-success"),
]);

function getTrendErrorMessage(error: unknown): string {
  if (error instanceof HttpError && error.code === ApiStatus.forbidden) {
    return "当前账号没有查看登录趋势的权限";
  }
  if (error instanceof HttpError && error.code === ApiStatus.unauthorized) {
    return "登录会话已失效，请重新登录";
  }
  return "登录趋势暂不可用，请稍后重试";
}

function isLoginTrendItems(value: unknown): value is LoginTrendItem[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        item &&
        typeof item === "object" &&
        typeof (item as LoginTrendItem).day === "string" &&
        Number.isFinite((item as LoginTrendItem).logins) &&
        Number.isFinite((item as LoginTrendItem).unique_users) &&
        Number.isFinite((item as LoginTrendItem).new_users)
    )
  );
}

async function loadTrend(): Promise<void> {
  if (loading.value) return;

  loading.value = true;
  trendError.value = "";
  isAuthBlocked.value = false;
  try {
    const response = await DashboardAPI.getLoginTrend();
    const items = response.data.data?.items;
    if (!isLoginTrendItems(items)) throw new Error("登录趋势响应格式无效");
    trendItems.value = items;
    hasLoaded.value = true;
  } catch (error: unknown) {
    trendItems.value = [];
    trendError.value = getTrendErrorMessage(error);
    if (
      error instanceof HttpError &&
      (error.code === ApiStatus.forbidden || error.code === ApiStatus.unauthorized)
    ) {
      isAuthBlocked.value = true;
      stopTimer();
    }
  } finally {
    loading.value = false;
  }
}

let refreshTimer: number | undefined;

function startTimer() {
  stopTimer();
  if (isAuthBlocked.value) {
    return;
  }
  refreshTimer = window.setInterval(() => void loadTrend(), REFRESH_INTERVAL);
}

function stopTimer() {
  if (refreshTimer !== undefined) {
    window.clearInterval(refreshTimer);
    refreshTimer = undefined;
  }
}

onMounted(() => {
  void loadTrend();
  startTimer();
});

onActivated(() => {
  if (hasLoaded.value) {
    startTimer();
  }
});

onDeactivated(() => {
  stopTimer();
});

onUnmounted(() => {
  stopTimer();
});
</script>

<style scoped lang="scss">
.login-trend-card {
  background: var(--fa-glass-bg, var(--el-bg-color));
  border: 1px solid var(--fa-glass-border, var(--el-border-color));
  border-radius: var(--fa-radius-panel);
  box-shadow: var(--fa-shadow-bento, var(--fa-soft-shadow));
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);
}

.login-trend-card :deep(.el-card__header) {
  padding: 24px 24px 0;
  border-bottom: 0;
}

.login-trend-card :deep(.el-card__body) {
  padding: 20px 24px;
}

.section-heading {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  justify-content: space-between;
}

.section-heading h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
  color: var(--el-text-color-primary);
}

.section-heading p {
  margin: 5px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.section-actions {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
}

.trend-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--fa-color-border, var(--el-border-color));
}

.trend-summary__item {
  display: grid;
  gap: 6px;
  padding: 4px 20px;
  border-right: 1px solid var(--fa-color-border, var(--el-border-color));
}

.trend-summary__item:first-child {
  padding-left: 0;
}

.trend-summary__item:last-child {
  border-right: 0;
}

.trend-summary__item span {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.trend-summary__item strong {
  font-size: 34px;
  font-weight: 680;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
  color: var(--el-text-color-primary);
}

.trend-chart {
  height: 220px;
  margin-top: 16px;
}

.trend-chart :deep(.relative) {
  width: 100%;
}

.trend-state {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 280px;
  padding: 20px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.trend-state--error {
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
  border-radius: 7px;
}

.trend-state__icon {
  font-size: 18px;
}

.trend-state__icon--spin {
  animation: trend-spin 1.1s linear infinite;
}

@keyframes trend-spin {
  to {
    transform: rotate(360deg);
  }
}

.section-actions :deep(button) {
  min-width: 44px;
  min-height: 44px;
}

.trend-details {
  margin-top: 16px;
  font-size: 12px;
  color: var(--fa-color-text-muted);
}

.trend-details summary {
  width: fit-content;
  min-height: 44px;
  padding: 12px 0;
  cursor: pointer;
}

.trend-details summary:focus-visible {
  outline: 2px solid var(--theme-color);
  outline-offset: 2px;
}

.trend-details__table {
  overflow-x: auto;
}

.trend-details table {
  width: 100%;
  font-variant-numeric: tabular-nums;
  text-align: right;
  border-collapse: collapse;
}

.trend-details th,
.trend-details td {
  padding: 10px 4px;
  font-weight: 400;
  white-space: nowrap;
  border-bottom: 1px solid var(--fa-color-border);
}

.trend-details th:first-child {
  text-align: left;
}

@media (width <= 640px) {
  .login-trend-card :deep(.el-card__body),
  .login-trend-card :deep(.el-card__header) {
    padding: 16px;
  }

  .trend-summary__item {
    padding: 4px 10px;
  }

  .trend-summary__item strong {
    font-size: 23px;
  }

  .trend-summary__item span {
    font-size: 11px;
  }

  .trend-chart {
    height: 240px;
  }

  .trend-state {
    min-height: 240px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .trend-state__icon--spin {
    animation: none;
  }
}
</style>
