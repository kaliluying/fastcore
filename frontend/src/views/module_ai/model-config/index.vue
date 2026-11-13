<template>
  <div class="model-config-page">
    <FaAiPageHeader
      title="模型配置"
      description="配置对话模型的连接方式，并查看当前向量运行配置。"
    />

    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <span>对话模型</span>
            <ElTag effect="plain" type="info">{{ protocolLabel }}</ElTag>
          </div>
          <div class="header-actions">
            <ElButton :icon="Refresh" :loading="loading" @click="loadConfig">刷新</ElButton>
            <ElButton
              v-auth="'module_ai:model_config:update'"
              type="primary"
              :icon="Check"
              :loading="saving"
              :disabled="loading || loadError"
              @click="saveConfig"
            >
              保存
            </ElButton>
          </div>
        </div>
      </template>

      <FaAsyncState
        v-if="loadError"
        state="error"
        title="模型配置加载失败"
        description="当前输入已保留，请重新加载后再保存。"
      >
        <template #action
          ><ElButton :loading="loading" @click="loadConfig">重试</ElButton></template
        >
      </FaAsyncState>

      <ElForm
        ref="formRef"
        v-loading="loading"
        :model="form"
        :rules="rules"
        class="model-form"
        label-width="104px"
        :label-position="isNarrowViewport ? 'top' : 'right'"
      >
        <ElFormItem label="接口协议" prop="chat_protocol">
          <ElSelect v-model="form.chat_protocol" :disabled="!canEdit" class="form-control">
            <ElOption label="OpenAI · Chat Completions" value="openai" />
            <ElOption label="OpenAI · Responses" value="openai_responses" />
            <ElOption label="Anthropic Claude" value="anthropic" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="API 地址" prop="openai_base_url">
          <ElInput v-model="form.openai_base_url" :disabled="!canEdit" autocomplete="url" />
        </ElFormItem>
        <ElFormItem label="模型名称" prop="openai_model">
          <div class="model-picker">
            <ElAutocomplete
              ref="modelInputRef"
              v-model="form.openai_model"
              :disabled="!canEdit"
              :fetch-suggestions="suggestModels"
              placeholder="输入模型名称，或获取后选择"
              class="model-picker-input"
              @input="showAllModels = false"
            />
            <ElButton
              :icon="Refresh"
              :loading="fetchingModels"
              :disabled="!canEdit"
              @click="fetchModels"
              >获取模型</ElButton
            >
          </div>
        </ElFormItem>
        <ElFormItem label="API Key" prop="openai_api_key">
          <ElInput
            v-model="form.openai_api_key"
            :disabled="!canEdit"
            type="password"
            show-password
            autocomplete="new-password"
          />
          <ElTag
            class="key-status"
            :type="config?.openai_api_key_configured ? 'success' : 'danger'"
            effect="plain"
          >
            {{ config?.openai_api_key_configured ? "已配置" : "未配置" }}
          </ElTag>
        </ElFormItem>
      </ElForm>

      <ElDivider content-position="left">模型能力验证</ElDivider>
      <p class="check-note">
        使用当前输入实际请求回答、流式输出和工具调用，最多 3
        次调用，会消耗用户额度并可能产生供应商费用；不会保存配置。
      </p>
      <ElButton
        v-auth="'module_ai:model_config:update'"
        :loading="checking"
        :disabled="loading || loadError || !canEdit"
        @click="checkCapabilities"
        >验证 3 项能力</ElButton
      >
      <ElDescriptions
        v-if="capabilityCheck"
        :column="1"
        border
        class="check-results"
        aria-live="polite"
      >
        <ElDescriptionsItem
          v-for="result in capabilityCheck.results"
          :key="result.capability"
          :label="capabilityLabels[result.capability]"
        >
          <ElTag :type="result.status === 'passed' ? 'success' : 'danger'">{{
            result.status === "passed" ? "通过" : "失败"
          }}</ElTag>
          {{ result.message }} · {{ result.latency_ms }} ms
        </ElDescriptionsItem>
      </ElDescriptions>
      <p v-if="checkError" role="alert">{{ checkError }}</p>

      <ElDivider content-position="left">向量运行状态</ElDivider>
      <ElDescriptions :column="isNarrowViewport ? 1 : 2" border>
        <ElDescriptionsItem label="向量来源">
          <ElTag :type="config?.embedding_provider === 'local' ? 'success' : 'warning'">
            {{ config?.embedding_provider || "-" }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="向量模型">{{ embeddingModelLabel }}</ElDescriptionsItem>
        <ElDescriptionsItem label="Chroma 持久化目录">{{
          config?.chroma_persist_dir || "-"
        }}</ElDescriptionsItem>
        <ElDescriptionsItem label="Chroma 集合">{{
          config?.chroma_collection_name || "-"
        }}</ElDescriptionsItem>
      </ElDescriptions>
    </ElCard>
    <ElCard shadow="never" class="usage-card"><UsagePanel :self-only="false" /></ElCard>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { ElMessage, type FormInstance, type FormRules } from "element-plus";
import FaAiPageHeader from "@/views/module_ai/components/FaAiPageHeader.vue";
import FaAsyncState from "@/components/feedback/fa-async-state/index.vue";
import { Check, Refresh } from "@element-plus/icons-vue";
import { useAuth } from "@/hooks/core/useAuth";
import UsagePanel from "./UsagePanel.vue";
import AiChatAPI, {
  type AiModelConfig,
  type AiModelConfigUpdate,
  type AiModelCapabilityCheck,
} from "@/api/module_ai/chat";

defineOptions({ name: "AiModelConfig" });

const checking = ref(false);
const checkError = ref("");
const capabilityCheck = ref<AiModelCapabilityCheck>();
const capabilityLabels = { complete: "普通回答", stream: "流式输出", tools: "工具调用" };
const checkCapabilities = async () => {
  await formRef.value?.validate();
  checking.value = true;
  checkError.value = "";
  capabilityCheck.value = undefined;
  const request = {
    ...form,
    ...(form.openai_api_key?.trim()
      ? { openai_api_key: form.openai_api_key.trim() }
      : { openai_api_key: undefined }),
  };
  const selection = JSON.stringify(form);
  try {
    const res = await AiChatAPI.checkModelCapabilities(request);
    if (selection === JSON.stringify(form)) capabilityCheck.value = res.data.data;
  } catch {
    checkError.value = "验证失败，请检查配置或稍后重试。";
  } finally {
    checking.value = false;
  }
};

const loading = ref(false);
const loadError = ref(false);
const isNarrowViewport = useMediaQuery("(max-width: 640px)");
const saving = ref(false);
const fetchingModels = ref(false);
const showAllModels = ref(false);
const modelOptions = ref<{ value: string }[]>([]);
const modelInputRef = ref<{ focus: () => void }>();
const config = ref<AiModelConfig>();
const formRef = ref<FormInstance>();
const { hasAuth } = useAuth();
const canEdit = computed(() => hasAuth("module_ai:model_config:update"));
const form = reactive<AiModelConfigUpdate>({
  chat_protocol: "openai",
  openai_base_url: "",
  openai_model: "",
  openai_api_key: "",
});
const rules: FormRules<AiModelConfigUpdate> = {
  openai_base_url: [{ required: true, message: "请输入 API 地址", trigger: "blur" }],
  openai_model: [{ required: true, message: "请输入模型名称", trigger: "blur" }],
};

const embeddingModelLabel = computed(() => {
  if (!config.value) return "-";
  return config.value.embedding_provider === "local"
    ? config.value.local_embedding_model || "-"
    : config.value.openai_embedding_model || "-";
});

const protocolLabel = computed(
  () =>
    ({
      openai: "OpenAI · Chat Completions",
      openai_responses: "OpenAI · Responses",
      anthropic: "Anthropic Claude",
    })[form.chat_protocol]
);

const suggestModels = (query: string, callback: (items: { value: string }[]) => void) => {
  if (showAllModels.value) {
    callback(modelOptions.value);
    return;
  }
  const normalized = query.trim().toLowerCase();
  callback(modelOptions.value.filter((item) => item.value.toLowerCase().includes(normalized)));
};

watch(
  () => [form.chat_protocol, form.openai_base_url, form.openai_api_key, form.openai_model],
  ([newProto, newUrl, newKey], oldVal) => {
    capabilityCheck.value = undefined;
    const [oldProto, oldUrl, oldKey] = oldVal ?? [];
    if (newProto !== oldProto || newUrl !== oldUrl || newKey !== oldKey) {
      modelOptions.value = [];
      showAllModels.value = false;
    }
  }
);

const fetchModels = async () => {
  fetchingModels.value = true;
  const selection = [form.chat_protocol, form.openai_base_url, form.openai_api_key].join("\n");
  try {
    const res = await AiChatAPI.listProviderModels({
      chat_protocol: form.chat_protocol,
      openai_base_url: form.openai_base_url,
      ...(form.openai_api_key?.trim() ? { openai_api_key: form.openai_api_key.trim() } : {}),
    });
    if (selection !== [form.chat_protocol, form.openai_base_url, form.openai_api_key].join("\n"))
      return;
    modelOptions.value = (res.data.data || []).map((value) => ({ value }));
    showAllModels.value = true;
    await nextTick();
    modelInputRef.value?.focus();
    ElMessage.success(`已获取 ${modelOptions.value.length} 个模型，可输入名称筛选并选择`);
  } finally {
    fetchingModels.value = false;
  }
};

const applyConfig = (value?: AiModelConfig) => {
  config.value = value;
  if (!value) return;
  form.chat_protocol = value.chat_protocol;
  form.openai_base_url = value.openai_base_url;
  form.openai_model = value.openai_model;
  form.openai_api_key = "";
};

const loadConfig = async () => {
  loading.value = true;
  loadError.value = false;
  try {
    const res = await AiChatAPI.getModelConfig();
    applyConfig(res.data?.data);
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
};

const saveConfig = async () => {
  await formRef.value?.validate();
  saving.value = true;
  try {
    const body: AiModelConfigUpdate = {
      chat_protocol: form.chat_protocol,
      openai_base_url: form.openai_base_url,
      openai_model: form.openai_model,
    };
    if (form.openai_api_key?.trim()) body.openai_api_key = form.openai_api_key.trim();
    const res = await AiChatAPI.updateModelConfig(body);
    applyConfig(res.data?.data);
    ElMessage.success("模型配置已保存");
  } finally {
    saving.value = false;
  }
};

onMounted(loadConfig);
</script>

<style scoped>
.usage-card,
.check-results {
  margin-top: 16px;
}

.check-note {
  line-height: 1.6;
  color: var(--fa-color-text-muted);
}

.model-config-page {
  min-width: 0;
}

.model-config-page :deep(.el-descriptions__cell) {
  overflow-wrap: anywhere;
}

.card-header {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.header-title,
.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.model-form {
  max-width: 760px;
}

.form-control {
  width: 100%;
}

.model-picker {
  display: flex;
  gap: 8px;
  width: 100%;
  min-width: 0;
}

.model-picker-input {
  flex: 1;
  min-width: 0;
}

.key-status {
  margin-top: 8px;
}

@media (width <= 640px) {
  .model-picker {
    flex-direction: column;
  }

  .model-picker-input {
    width: 100%;
  }

  .model-picker > .el-button {
    align-self: flex-start;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .header-actions {
    justify-content: flex-end;
    width: 100%;
  }
}
</style>
