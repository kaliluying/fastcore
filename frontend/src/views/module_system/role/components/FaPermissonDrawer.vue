<!-- 角色授权 -->
<template>
  <FaDrawer
    v-model="drawerVisible"
    :title="`${props.roleName}的权限`"
    :size="drawerSize"
    destroy-on-close
    @close="handleCancel"
  >
    <div class="drawer-perm-content flex flex-col flex-1 overflow-hidden">
      <ElContainer class="h-full min-h-0 flex-1">
        <!-- 数据权限 -->
        <ElAside class="permission-scope">
          <div>
            <h2>数据范围</h2>
            <p>选择角色可查看与操作的数据范围。</p>
            <div class="mt-3">
              <ElForm ref="dataFormRef" :model="permissionState">
                <ElFormItem prop="data_scope">
                  <ElSelect v-model="permissionState.data_scope" aria-label="数据权限范围">
                    <ElOption :key="1" label="仅本人数据权限" :value="1" />
                    <ElOption :key="4" label="全部数据权限" :value="4" />
                  </ElSelect>
                </ElFormItem>
              </ElForm>
            </div>
          </div>
        </ElAside>

        <!-- 菜单权限 -->
        <ElMain>
          <h2>菜单与操作权限</h2>
          <p>勾选可访问的菜单及对应操作，保存后生效。</p>
          <div class="mt-3 flex-1 min-h-0">
            <FaMenuTreeTable
              ref="menuTreeTableRef"
              :menu-tree="rawMenuTree as MenuTreeNode[]"
              :checked-ids="menuCheckedIds"
              :loading="loading"
            />
          </div>
        </ElMain>
      </ElContainer>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <ElButton @click="handleCancel">取消</ElButton>
        <ElButton type="primary" :loading="loading" @click.stop="handleDrawerSave"
          >保存权限</ElButton
        >
      </div>
    </template>
  </FaDrawer>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from "vue";
import FaMenuTreeTable from "@/components/others/fa-menu-tree-table/index.vue";
import type { MenuTreeNode } from "@/components/others/fa-menu-tree-table/composables/useMenuTreeTable";
import RoleAPI, { permissionDataType } from "@/api/module_system/role";
import MenuAPI, { MenuTable } from "@/api/module_platform/menu";
import { DeviceEnum } from "@/enums/settings/device.enum";
import { useAppStore, useUserStore } from "@stores";
import { refreshMenuAndRoutes } from "@/router/beforeEach";
import { ElMessage } from "element-plus";

const props = defineProps<{
  roleName: string;
  roleId: number;
  modelValue: boolean;
}>();

interface Emits {
  "update:modelValue": [v: boolean];
  saved: [];
}

const emit = defineEmits<Emits>();

const appStore = useAppStore();
const drawerSize = computed(() =>
  appStore.device === DeviceEnum.DESKTOP ? "min(1200px, calc(100vw - 24px))" : "calc(100vw - 24px)"
);

const drawerVisible = computed({
  get: () => props.modelValue,
  set(v) {
    emit("update:modelValue", v);
  },
});

const dataFormRef = ref();
const loading = ref(false);
const rawMenuTree = ref<MenuTable[]>([]);
const menuCheckedIds = ref<number[]>([]);
const menuTreeTableRef = ref<InstanceType<typeof FaMenuTreeTable>>();

const permissionState = ref<permissionDataType>({
  role_ids: [],
  menu_ids: [],
  data_scope: 1,
});

const init = async () => {
  loading.value = true;
  try {
    const [menuResponse, roleResponse] = await Promise.all([
      MenuAPI.listMenu(),
      RoleAPI.detailRole(props.roleId),
    ]);
    rawMenuTree.value = menuResponse.data.data || [];

    const roleData = roleResponse.data.data;
    const savedMenuIds = roleData?.menus?.map((menu: any) => menu.id) || [];

    permissionState.value = {
      role_ids: [props.roleId],
      menu_ids: savedMenuIds,
      data_scope: roleData?.data_scope || 1,
    };

    menuCheckedIds.value = savedMenuIds;
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error);
    ElMessage.error("获取权限数据失败: " + msg);
  } finally {
    loading.value = false;
  }
};

function handleCancel() {
  drawerVisible.value = false;
}

async function handleDrawerSave() {
  try {
    if (props.roleId === 1) {
      ElMessage.warning("系统默认角色，不可操作");
      return;
    }
    loading.value = true;

    const checkedIds = menuTreeTableRef.value?.getCheckedIds() ?? [];
    const menu_ids = expandMenuIdsWithAncestors(checkedIds, rawMenuTree.value);

    const submitData: permissionDataType = {
      role_ids: [props.roleId],
      menu_ids,
      data_scope: permissionState.value.data_scope,
    };

    await RoleAPI.setPermission(submitData);

    const userStore = useUserStore();
    await userStore.getUserInfo();
    await refreshMenuAndRoutes();

    drawerVisible.value = false;
    emit("saved");
  } catch (error: unknown) {
    console.error(error);
  } finally {
    loading.value = false;
  }
}

/** 将选中的菜单/按钮 ID 展开为包含所有祖先菜单的完整集合 */
function expandMenuIdsWithAncestors(checkedIds: number[], roots: MenuTable[]): number[] {
  const parentById = new Map<number, number | undefined>();
  const walk = (nodes: MenuTable[], parent: number | undefined) => {
    for (const n of nodes) {
      const id = n.id!;
      parentById.set(id, parent);
      if (n.children?.length) walk(n.children as MenuTable[], id);
    }
  };
  walk(roots, undefined);
  const out = new Set<number>();
  for (const id of checkedIds) {
    let cur: number | undefined = id;
    while (cur !== undefined) {
      out.add(cur);
      cur = parentById.get(cur);
    }
  }
  return [...out];
}

onMounted(async () => {
  await init();
});
</script>

<style scoped lang="scss">
.drawer-perm-content {
  min-width: 0;

  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--fa-color-text);
  }

  p {
    margin: var(--fa-space-2) 0 var(--fa-space-4);
    font-size: 13px;
    line-height: 1.6;
    color: var(--fa-color-text-muted);
  }

  :deep(.el-main) {
    min-width: 0;
  }
}

.permission-scope {
  width: 260px;
  padding: var(--fa-space-5);
  border-right: 1px solid var(--fa-color-border);
}

.dialog-footer {
  display: flex;
  gap: var(--fa-space-2);
  justify-content: flex-end;

  :deep(button:focus-visible) {
    outline: 2px solid var(--el-color-primary);
    outline-offset: 2px;
  }
}

@media (width <= 640px) {
  .drawer-perm-content {
    overflow: auto;

    :deep(.el-container) {
      flex: none;
      flex-direction: column;
    }

    :deep(.el-select__wrapper),
    :deep(.el-checkbox) {
      min-height: 44px;
    }
  }

  .permission-scope {
    width: 100%;
    overflow: visible;
    border-right: 0;
    border-bottom: 1px solid var(--fa-color-border);
  }

  .dialog-footer :deep(.el-button) {
    min-height: 44px;
    margin-left: 0;
  }
}
</style>
