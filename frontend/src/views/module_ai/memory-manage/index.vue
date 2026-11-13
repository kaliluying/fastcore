<!-- AI 记忆管理：对 user_preference / fact / work_rule 进行增删改查 -->
<template>
  <div class="fa-full-height memory-page">
    <FaAiPageHeader
      title="记忆管理"
      description="管理回答参考的用户偏好、事实和工作规则，决定哪些记忆继续生效。"
    />

    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :rules="searchBarRules"
      :is-expand="false"
      :show-expand="false"
      :show-reset="true"
      :show-search="true"
      :disabled-search="false"
      :default-expanded="true"
      @search="handleSearch"
      @reset="onResetSearch"
    />

    <ElCard
      shadow="never"
      class="fa-table-card"
      :style="{ 'margin-top': showSearchBar ? '12px' : '0' }"
    >
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="loadData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['module_ai:memory:create']"
            :perm-delete="['module_ai:memory:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
        </template>
      </FaTableHeader>

      <FaAsyncState
        v-if="loadError"
        state="error"
        title="记忆列表加载失败"
        description="已保留当前列表和筛选，请重试。"
      >
        <template #action><ElButton :loading="loading" @click="loadData">重试</ElButton></template>
      </FaAsyncState>

      <FaTable
        ref="faTableRef"
        row-key="id"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        empty-text="没有匹配的记忆，可调整筛选；需要时按权限添加偏好或规则"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      >
        <template #memory-type="{ row }">
          <ElTag :type="memoryTypeTag(row.memory_type)" size="small">
            {{ memoryTypeLabel(row.memory_type) }}
          </ElTag>
        </template>
        <template #memory-status="{ row }">
          <ElSwitch
            :model-value="row.is_active"
            size="small"
            :aria-label="`启用记忆：${row.key}`"
            @change="(val: string | number | boolean) => handleToggleActive(row, Boolean(val))"
          />
        </template>
        <template #actions="{ row }">
          <ElButton type="primary" link size="small" @click="handleEdit(row)">编辑</ElButton>
          <ElButton type="danger" link size="small" @click="handleDeleteOne(row)">删除</ElButton>
        </template>
      </FaTable>
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="min(92vw, 600px)"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @confirm="handleDialogConfirm"
      @closed="handleDialogClosed"
    >
      <ElForm
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-width="80px"
        :label-position="isNarrowViewport ? 'top' : 'right'"
      >
        <ElFormItem label="记忆类型" prop="memory_type">
          <ElSelect
            v-model="formData.memory_type"
            :disabled="dialogVisible.type === 'update'"
            class="w-full"
          >
            <ElOption label="用户偏好" value="user_preference" />
            <ElOption label="用户事实" value="fact" />
            <ElOption label="工作规则" value="work_rule" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="标签" prop="key">
          <ElInput v-model="formData.key" placeholder="如: 用户角色" maxlength="128" />
        </ElFormItem>
        <ElFormItem label="内容" prop="value">
          <ElInput
            v-model="formData.value"
            type="textarea"
            :rows="4"
            placeholder="如: 偏好先看执行摘要，再看明细"
            maxlength="5000"
            show-word-limit
          />
        </ElFormItem>
        <ElFormItem label="分类" prop="category">
          <ElInput
            v-model="formData.category"
            placeholder="可选分组，如: 用户信息"
            maxlength="64"
          />
        </ElFormItem>
        <ElFormItem label="优先级" prop="priority">
          <ElInputNumber v-model="formData.priority" :min="0" :max="100" />
          <span class="form-tip">数值越高，回答时越优先参考</span>
        </ElFormItem>
        <ElFormItem label="启用" prop="is_active">
          <ElSwitch v-model="formData.is_active" />
        </ElFormItem>
      </ElForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from "vue";
import { useMediaQuery } from "@vueuse/core";
import FaAsyncState from "@/components/feedback/fa-async-state/index.vue";
import FaAiPageHeader from "@/views/module_ai/components/FaAiPageHeader.vue";
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from "element-plus";
import {
  AiMemoryAPI,
  type AiMemoryItem,
  type MemoryCreatePayload,
  type MemoryType,
} from "@/api/module_ai/memory";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { ColumnOption } from "@/types/component";

defineOptions({ name: "AiMemoryManage" });

// ── 搜索栏 ──
const showSearchBar = ref(true);
const searchForm = ref<{ memory_type?: string; key?: string }>({});
const searchItems = ref<SearchFormItem[]>([
  {
    key: "memory_type",
    label: "记忆类型",
    type: "select",
    options: [
      { label: "全部", value: "" },
      { label: "用户偏好", value: "user_preference" },
      { label: "用户事实", value: "fact" },
      { label: "工作规则", value: "work_rule" },
    ],
  },
  { key: "key", label: "标签搜索", type: "input", placeholder: "模糊搜索标签" },
]);
const searchBarRules: FormRules = {};

const memoryTypeLabel = (type: string) =>
  (
    ({ user_preference: "用户偏好", fact: "用户事实", work_rule: "工作规则" }) as Record<
      string,
      string
    >
  )[type] || type;

const memoryTypeTag = (type: string): "primary" | "success" | "info" | "warning" | "danger" =>
  (({ user_preference: "primary", fact: "success", work_rule: "warning" }) as const)[
    type as "user_preference" | "fact" | "work_rule"
  ] || "info";

// ── 表格 ──
const loading = ref(false);
const loadError = ref(false);
const isNarrowViewport = useMediaQuery("(max-width: 640px)");
const data = ref<AiMemoryItem[]>([]);
const selectedIds = ref<number[]>([]);
const batchDeleting = ref(false);
const createLoading = ref(false);

const columnChecks = ref([
  { prop: "memory_type", label: "类型", visible: true },
  { prop: "category", label: "分类", visible: true },
  { prop: "key", label: "标签", visible: true },
  { prop: "value", label: "内容", visible: true },
  { prop: "priority", label: "优先级", visible: true },
  { prop: "is_active", label: "启用", visible: true },
  { prop: "created_time", label: "创建时间", visible: true },
  { prop: "updated_time", label: "更新时间", visible: true },
]);

const columns = computed<ColumnOption<AiMemoryItem>[]>(() => [
  { type: "selection", width: 50, fixed: "left" },
  ...columnChecks.value
    .filter((c) => c.visible)
    .map((c) => ({
      prop: c.prop,
      label: c.label,
      minWidth: c.prop === "value" ? 250 : c.prop === "key" ? 150 : 100,
      showOverflowTooltip: c.prop === "value",
      useSlot: c.prop === "memory_type" || c.prop === "is_active",
      slotName:
        c.prop === "memory_type"
          ? "memory-type"
          : c.prop === "is_active"
            ? "memory-status"
            : undefined,
    })),
  { label: "操作", width: 140, fixed: "right", useSlot: true, slotName: "actions" },
]);

const pagination = reactive({
  current: 1,
  size: 20,
  total: 0,
});

// ── 数据加载 ──
async function loadData() {
  loading.value = true;
  loadError.value = false;
  try {
    const res = await AiMemoryAPI.list({
      page_no: pagination.current,
      page_size: pagination.size,
      memory_type: (searchForm.value.memory_type || undefined) as MemoryType | undefined,
      key: searchForm.value.key || undefined,
    });
    const body = res.data.data;
    data.value = body.items || [];
    pagination.total = body.total || 0;
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  pagination.current = 1;
  loadData();
}
function onResetSearch() {
  searchForm.value = {
    memory_type: "",
    key: "",
  };
  handleSearch();
}
function handleSizeChange(val: number) {
  pagination.size = val;
  loadData();
}
function handleCurrentChange(val: number) {
  pagination.current = val;
  loadData();
}
function onTableSelectionChange(rows: AiMemoryItem[]) {
  selectedIds.value = rows.map((row) => row.id);
}

// ── 批量删除 ──
async function handleBatchDelete() {
  if (!selectedIds.value.length) {
    ElMessage.warning("请先选择要删除的记录");
    return;
  }
  try {
    await ElMessageBox.confirm(`确定要删除 ${selectedIds.value.length} 条记忆吗？`, "删除确认", {
      type: "warning",
    });
    batchDeleting.value = true;
    await AiMemoryAPI.delete(selectedIds.value);
    ElMessage.success("批量删除成功");
    selectedIds.value = [];
    loadData();
  } catch (e: any) {
    if (e !== "cancel") ElMessage.error(e?.message || "删除失败");
  } finally {
    batchDeleting.value = false;
  }
}

async function handleDeleteOne(row: AiMemoryItem) {
  try {
    await ElMessageBox.confirm("确定要删除这条记忆吗？", "删除确认", { type: "warning" });
    await AiMemoryAPI.delete([row.id]);
    ElMessage.success("删除成功");
    loadData();
  } catch (e: any) {
    if (e !== "cancel") ElMessage.error(e?.message || "删除失败");
  }
}

// ── 对话框 ──
const dialogVisible = reactive({
  visible: false,
  title: "",
  type: "create" as "create" | "update",
});
const submitLoading = ref(false);
const formRef = ref<FormInstance>();
const editingId = ref<number | null>(null);

const formData = reactive<MemoryCreatePayload & { memory_type: MemoryType }>({
  memory_type: "fact",
  category: null,
  key: "",
  value: "",
  priority: 0,
  is_active: true,
});

const formRules: FormRules = {
  memory_type: [{ required: true, message: "请选择记忆类型", trigger: "change" }],
  key: [{ required: true, message: "请输入标签", trigger: "blur" }],
  value: [{ required: true, message: "请输入内容", trigger: "blur" }],
};

function handleAdd() {
  dialogVisible.type = "create";
  dialogVisible.title = "新增记忆";
  formData.memory_type = "fact";
  formData.category = null;
  formData.key = "";
  formData.value = "";
  formData.priority = 0;
  formData.is_active = true;
  editingId.value = null;
  dialogVisible.visible = true;
}

function handleEdit(row: AiMemoryItem) {
  dialogVisible.type = "update";
  dialogVisible.title = "编辑记忆";
  formData.memory_type = row.memory_type;
  formData.category = row.category;
  formData.key = row.key;
  formData.value = row.value;
  formData.priority = row.priority;
  formData.is_active = row.is_active;
  editingId.value = row.id;
  dialogVisible.visible = true;
}

function handleDialogClosed() {
  formRef.value?.resetFields();
}

async function handleDialogConfirm() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;
  submitLoading.value = true;
  try {
    if (dialogVisible.type === "create") {
      await AiMemoryAPI.create({
        memory_type: formData.memory_type,
        category: formData.category || null,
        key: formData.key,
        value: formData.value,
        priority: formData.priority,
        is_active: formData.is_active,
      });
      ElMessage.success("创建成功");
    } else if (editingId.value) {
      await AiMemoryAPI.update(editingId.value, {
        memory_type: formData.memory_type,
        category: formData.category || null,
        key: formData.key,
        value: formData.value,
        priority: formData.priority,
        is_active: formData.is_active,
      });
      ElMessage.success("更新成功");
    }
    dialogVisible.visible = false;
    loadData();
  } catch (e: any) {
    ElMessage.error(e?.message || "操作失败");
  } finally {
    submitLoading.value = false;
  }
}

async function handleToggleActive(row: AiMemoryItem, val: boolean) {
  try {
    await AiMemoryAPI.update(row.id, { is_active: val });
    ElMessage.success(val ? "已启用" : "已禁用");
    row.is_active = val;
  } catch (e: any) {
    ElMessage.error(e?.message || "切换失败");
  }
}

loadData();
</script>

<style scoped>
.form-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
