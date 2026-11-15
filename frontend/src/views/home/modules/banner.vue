<template>
  <header class="home-header">
    <div class="header-titles">
      <div class="status-badge" aria-hidden="true">
        <span class="badge-dot" />
        <span>AI 知识管理</span>
      </div>
      <h1>工作台</h1>
      <p>
        欢迎回来<span v-if="userName">，{{ userName }}</span
        >。聚焦智能知识检索与核心业务运转。
      </p>
    </div>
    <div class="home-health" :class="`home-health--${healthState}`" role="status">
      <button
        type="button"
        :disabled="healthLoading"
        :aria-label="`刷新基础服务状态：${healthLoading ? healthLabels.loading : healthLabels[healthState]}`"
        @click="$emit('refresh')"
      >
        <span class="home-health__dot" aria-hidden="true" />
        <span class="health-text">{{
          healthLoading ? "正在检查基础服务" : healthLabels[healthState]
        }}</span>
        <FaSvgIcon
          icon="ri:refresh-line"
          :class="{ 'spin-icon': healthLoading }"
          aria-hidden="true"
        />
      </button>
      <p v-if="healthState === 'degraded' || healthState === 'unavailable'" class="health-desc">
        {{ healthDescription }}
      </p>
    </div>
  </header>
</template>

<script setup lang="ts">
defineOptions({ name: "HomeBanner" });

defineProps<{
  healthState: "loading" | "healthy" | "degraded" | "unavailable";
  healthDescription: string;
  healthLoading: boolean;
  userName: string;
}>();

defineEmits<{ refresh: [] }>();

const healthLabels = {
  loading: "正在检查基础服务",
  healthy: "基础服务正常",
  degraded: "基础服务异常 · 重试",
  unavailable: "状态暂不可用 · 重试",
};
</script>

<style scoped lang="scss">
.home-header {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0 24px;
}

.header-titles {
  display: flex;
  flex-direction: column;
}

.status-badge {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  width: fit-content;
  padding: 2px 8px;
  margin-bottom: 6px;
  font-size: 11px;
  font-weight: 700;
  color: var(--theme-color);
  letter-spacing: 0.02em;
  background: color-mix(in srgb, var(--theme-color) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--theme-color) 25%, transparent);
  border-radius: 9999px;
}

.badge-dot {
  width: 5px;
  height: 5px;
  background: var(--theme-color);
  border-radius: 50%;
  box-shadow: 0 0 6px var(--theme-color);
}

h1 {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--fa-color-text);
  letter-spacing: -0.02em;
}

.home-header > div > p {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--fa-color-text-muted);
}

.home-health {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: 100%;
  font-size: 12.5px;
  color: var(--fa-color-text-muted);
}

.home-health button {
  display: inline-flex;
  gap: 9px;
  align-items: center;
  min-height: 44px;
  padding: 8px 16px;
  font: inherit;
  font-weight: 600;
  color: var(--fa-color-text);
  cursor: pointer;
  background: var(--fa-glass-bg, var(--fa-color-surface));
  border: 1px solid var(--fa-glass-border, var(--fa-color-border));
  border-radius: 9999px;
  box-shadow: var(--fa-shadow-bento, var(--fa-soft-shadow));
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.home-health button:hover {
  background: var(--fa-glass-bg-hover, var(--fa-color-surface));
  border-color: color-mix(in srgb, var(--theme-color) 35%, var(--fa-glass-border));
  box-shadow: var(--fa-shadow-bento-hover);
  transform: translateY(-1px);
}

.home-health button:disabled {
  cursor: wait;
  opacity: 0.8;
}

.home-health button:focus-visible {
  outline: 2px solid var(--theme-color);
  outline-offset: 3px;
}

.home-health__dot {
  width: 8px;
  height: 8px;
  background: var(--fa-color-text-muted);
  border-radius: 50%;
  transition: background 0.2s ease;
}

.home-health--healthy .home-health__dot {
  background: var(--fa-color-success);
  box-shadow: 0 0 8px var(--fa-color-success);
  animation: pulse-glow 2s infinite ease-in-out;
}

.home-health--degraded .home-health__dot,
.home-health--unavailable .home-health__dot {
  background: var(--fa-color-warning);
  box-shadow: 0 0 8px var(--fa-color-warning);
}

@keyframes pulse-glow {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }

  50% {
    opacity: 0.5;
    transform: scale(0.85);
  }
}

.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.health-desc {
  max-width: 320px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--fa-color-warning-text);
  text-align: right;
}

@media (width <= 520px) {
  .home-header {
    margin-bottom: 20px;
  }

  .home-health {
    align-items: flex-start;
  }

  .health-desc {
    text-align: left;
  }

  h1 {
    font-size: 24px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-health button,
  .home-health__dot {
    transition: none !important;
    animation: none !important;
  }
}
</style>
