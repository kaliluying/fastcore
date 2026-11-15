<!-- 首页业务概览：知识库、文档与 AI 用量，数据缺失时只渲染已就绪的指标 -->
<template>
  <section class="workspace-metrics" aria-label="知识工作概览">
    <article v-if="stats" class="workspace-metric">
      <span class="workspace-metric__label">知识库</span>
      <strong class="workspace-metric__value">{{ stats.knowledge_bases }}</strong>
      <span class="workspace-metric__hint">可检索的知识集合</span>
    </article>
    <article v-if="stats" class="workspace-metric">
      <span class="workspace-metric__label">文档</span>
      <strong class="workspace-metric__value">{{ stats.documents }}</strong>
      <span class="workspace-metric__hint">已索引 {{ stats.indexed_documents }}</span>
    </article>
    <article
      v-if="stats"
      class="workspace-metric"
      :class="{ 'workspace-metric--warning': stats.failed_documents > 0 }"
    >
      <span class="workspace-metric__label">解析失败</span>
      <strong class="workspace-metric__value">{{ stats.failed_documents }}</strong>
      <span class="workspace-metric__hint">
        {{ stats.failed_documents > 0 ? "需要重新索引" : "全部处理正常" }}
      </span>
    </article>
    <article v-if="usage" class="workspace-metric">
      <span class="workspace-metric__label">今日 AI 调用</span>
      <strong class="workspace-metric__value">{{ usage.calls }}</strong>
      <span class="workspace-metric__hint">
        {{
          usage.daily_limit === null
            ? "无每日额度限制"
            : `剩余 ${usage.remaining ?? 0} / ${usage.daily_limit}`
        }}
      </span>
    </article>
  </section>
</template>

<script setup lang="ts">
import type { AiUsageSummary } from "@/api/module_ai/chat";
import type { KnowledgeStats } from "@/api/module_ai/knowledge";

defineOptions({ name: "WorkspaceMetrics" });

defineProps<{ stats: KnowledgeStats | null; usage: AiUsageSummary | null }>();
</script>

<style scoped lang="scss">
.workspace-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 190px), 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.workspace-metric {
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 18px 20px;
  background: var(--fa-glass-bg, var(--fa-color-surface));
  border: 1px solid var(--fa-glass-border, var(--fa-color-border));
  border-radius: var(--fa-radius-panel);
  box-shadow: var(--fa-shadow-bento, var(--fa-soft-shadow));
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);
}

.workspace-metric--warning {
  border-color: color-mix(in srgb, var(--el-color-warning) 45%, var(--fa-glass-border));
}

.workspace-metric__label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--fa-color-text-muted);
}

.workspace-metric__value {
  font-size: 30px;
  font-weight: 680;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  color: var(--fa-color-text);
}

.workspace-metric--warning .workspace-metric__value {
  color: var(--el-color-warning-dark-2);
}

.workspace-metric__hint {
  font-size: 12.5px;
  color: var(--fa-color-text-muted);
}

@media (width <= 520px) {
  .workspace-metrics {
    gap: 12px;
    margin-bottom: 20px;
  }

  .workspace-metric {
    padding: 14px 16px;
  }

  .workspace-metric__value {
    font-size: 24px;
  }
}
</style>
