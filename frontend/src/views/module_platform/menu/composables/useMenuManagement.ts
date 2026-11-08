import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { useAppStore, useUserStore } from "@stores";
import { DeviceEnum } from "@/enums/settings/device.enum";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type FaForm from "@/components/forms/fa-form/index.vue";
import MenuAPI, {
  type MenuForm,
  type MenuPageQuery,
  type MenuTable,
} from "@/api/module_platform/menu";
import { MenuTypeEnum } from "@/enums/system/menu.enum";
import { formatTree } from "@utils/common";
import { refreshMenuAndRoutes } from "@/router/beforeEach";
import { ElMessage, ElMessageBox } from "element-plus";
import { buildMenuFormItems } from "../config/menuFormItems";
import { buildMenuRules } from "../config/menuRules";
import type { MenuRowActionContext } from "../config/menuColumns";

type MenuSearchForm = {
  name?: string;
  status?: number;
  created_time?: string[];
};

const initialFormData: MenuForm = {
  id: undefined,
  name: undefined,
  type: MenuTypeEnum.MENU,
  icon: undefined,
  order: 1,
  permission: "",
  route_name: "",
  route_path: "",
  component_path: "",
  redirect: "",
  parent_id: undefined,
  keep_alive: false,
  hidden: false,
  title: "",
  affix: false,
  link: undefined,
  is_iframe: false,
  is_hide_tab: false,
  active_path: undefined,
  show_badge: false,
  show_text_badge: undefined,
  status: 0,
  description: undefined,
};

function buildMenuListQuery(p: MenuSearchForm): MenuPageQuery {
  return {
    name: p.name,
    status: p.status,
    created_time:
      Array.isArray(p.created_time) && p.created_time.length === 2 ? p.created_time : undefined,
  };
}

function typesAllowedUnderParent(parentType: MenuTypeEnum): MenuTypeEnum[] {
  switch (parentType) {
    case MenuTypeEnum.CATALOG:
      return [MenuTypeEnum.CATALOG, MenuTypeEnum.MENU, MenuTypeEnum.EXTLINK];
    case MenuTypeEnum.MENU:
      return [MenuTypeEnum.BUTTON];
    case MenuTypeEnum.BUTTON:
    case MenuTypeEnum.EXTLINK:
      return [];
    default:
      return [MenuTypeEnum.CATALOG, MenuTypeEnum.MENU, MenuTypeEnum.EXTLINK];
  }
}

function filterMenuTypes(nodes: MenuTable[]): MenuTable[] {
  return nodes
    .filter((node) => node.type === MenuTypeEnum.CATALOG || node.type === MenuTypeEnum.MENU)
    .map((node: MenuTable & { children?: MenuTable[] }) => ({
      ...node,
      children: node.children ? filterMenuTypes(node.children) : [],
    }));
}

/**
 * 菜单管理的列表、表单与增删改状态
 *
 * @returns 页面模板所需的全部状态、派生值与事件处理函数。
 */
export function useMenuManagement() {
  const appStore = useAppStore();
  const userStore = useUserStore();

  /**
   * 重新加载当前管理员的权限和已注册的菜单路由。
   *
   * @returns 在侧栏与动态路由采用服务端最新菜单定义后完成的 Promise。
   */
  async function refreshAuthorizationUi(): Promise<void> {
    await userStore.getUserInfo();
    await refreshMenuAndRoutes();
  }

  // ---- 搜索 ----
  const searchForm = ref<MenuSearchForm>({
    name: undefined,
    status: undefined,
    created_time: undefined,
  });
  const showSearchBar = ref(true);
  const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
  const searchBarRules: Record<string, unknown> = {};

  const statusOptions = ref([
    { label: "启用", value: 0 },
    { label: "停用", value: 1 },
  ]);

  const menuSearchItems = computed<SearchFormItem[]>(() => [
    {
      label: "菜单名称",
      key: "name",
      type: "input",
      placeholder: "请输入菜单名称",
      clearable: true,
      span: 6,
    },
    {
      label: "状态",
      key: "status",
      type: "select",
      props: {
        placeholder: "请选择状态",
        options: statusOptions.value,
        clearable: true,
      },
      span: 6,
    },
  ]);

  // ---- 表格 ----
  const tableRef = ref<{
    elTableRef?: { toggleRowExpansion: (row: MenuTable, expanded?: boolean) => void };
  } | null>(null);
  const tableData = ref<MenuTable[]>([]);
  const loading = ref(false);
  const loadError = ref<{ message: string } | null>(null);
  const isExpanded = ref(false);
  const selectedRows = ref<MenuTable[]>([]);
  const selectedIds = computed(() =>
    selectedRows.value
      .map((r) => r.id)
      .filter((id): id is number => id != null && !Number.isNaN(id))
  );
  const batchDeleting = ref(false);
  const submitLoading = ref(false);
  const createLoading = ref(false);
  const moreLoading = ref(false);

  // ---- 表单 ----
  const menuOptions = ref<OptionType[]>([]);
  const fullMenuTree = ref<MenuTable[]>([]);
  const createParentLocked = ref(false);

  const detailFormData = ref<MenuTable>({});
  const formData = ref<MenuForm>({ ...initialFormData });
  const menuDialogFormItems = computed(() => buildMenuFormItems(formData));
  const rules = reactive(buildMenuRules(formData));

  const dialogVisible = reactive({
    title: "",
    visible: false,
    type: "create" as "create" | "update" | "detail",
  });

  const drawerSize = computed(() =>
    appStore.device === DeviceEnum.DESKTOP ? "min(900px, calc(100vw - 24px))" : "calc(100vw - 24px)"
  );

  const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
  const menuFormRenderKey = ref(0);
  const selectedMenuId = ref<number | undefined>();

  function findMenuNodeById(
    id: number | undefined,
    nodes: MenuTable[] = fullMenuTree.value
  ): MenuTable | null {
    if (id == null) return null;
    for (const n of nodes) {
      if (n.id === id) return n;
      if (n.children?.length) {
        const f = findMenuNodeById(id, n.children);
        if (f) return f;
      }
    }
    return null;
  }

  /**
   * 收集菜单自身及其全部后代，防止将它们选作父级。
   *
   * @param id 正在编辑的菜单。
   * @param nodes 父级选择器使用的完整菜单树。
   * @returns 父级选择器中必须禁用的 ID 集合。
   */
  function collectInvalidParentIds(id: number | undefined, nodes: MenuTable[]): Set<number> {
    const invalidIds = new Set<number>();
    if (id == null) return invalidIds;

    const collect = (node: MenuTable) => {
      if (node.id != null) invalidIds.add(node.id);
      node.children?.forEach(collect);
    };

    const findAndCollect = (items: MenuTable[]): boolean => {
      for (const node of items) {
        if (node.id === id) {
          collect(node);
          return true;
        }
        if (node.children?.length && findAndCollect(node.children)) return true;
      }
      return false;
    };

    findAndCollect(nodes);
    return invalidIds;
  }

  /**
   * 重建父级选择器选项，并禁用正在编辑的菜单的后代。
   *
   * @returns 无返回值；更新当前表单使用的选择器选项。
   */
  function refreshMenuOptions(): void {
    const invalidParentIds = collectInvalidParentIds(formData.value.id, fullMenuTree.value);
    const disableInvalidParents = (options: OptionType[]): OptionType[] =>
      options.map((option) => ({
        ...option,
        disabled: option.disabled || invalidParentIds.has(Number(option.value)),
        children: option.children ? disableInvalidParents(option.children) : undefined,
      }));

    menuOptions.value = disableInvalidParents(formatTree(filterMenuTypes(fullMenuTree.value)));
  }

  const allowedMenuTypeValues = computed(() => {
    const pid = formData.value.parent_id;
    const parentNode = findMenuNodeById(pid);
    if (!parentNode?.type) {
      return [MenuTypeEnum.CATALOG, MenuTypeEnum.MENU, MenuTypeEnum.EXTLINK];
    }
    return typesAllowedUnderParent(parentNode.type as MenuTypeEnum);
  });

  // ---- 列表加载 ----
  async function loadMenuData() {
    loading.value = true;
    loadError.value = null;
    try {
      const res = await MenuAPI.listMenu({
        ...buildMenuListQuery(searchForm.value),
      });
      const tree = res.data.data || [];
      fullMenuTree.value = tree;
      tableData.value = tree;
      refreshMenuOptions();
    } catch (e: unknown) {
      loadError.value = { message: e instanceof Error ? e.message : "加载失败，请重试" };
      console.error(e);
    } finally {
      loading.value = false;
    }
  }

  async function handleSearchBarSearch(params: MenuSearchForm) {
    await searchBarRef.value?.validate?.();
    searchForm.value = { ...params };
    await loadMenuData();
  }

  function onResetSearch() {
    searchForm.value = {
      name: undefined,
      status: undefined,
      created_time: undefined,
    };
    void loadMenuData();
  }

  function onTableSelectionChange(rows: MenuTable[]) {
    selectedRows.value = rows;
  }

  function toggleExpand() {
    isExpanded.value = !isExpanded.value;
    nextTick(() => {
      const el = tableRef.value?.elTableRef;
      if (!el || !tableData.value.length) return;
      const walk = (rows: MenuTable[]) => {
        rows.forEach((row) => {
          if (row.children?.length) {
            el.toggleRowExpansion(row, isExpanded.value);
            walk(row.children);
          }
        });
      };
      walk(tableData.value);
    });
  }

  async function handleRowClick(row: MenuTable) {
    selectedMenuId.value = row.id;
  }

  // ---- 删除与批量操作 ----
  async function deleteMenuRow(id: number) {
    try {
      await ElMessageBox.confirm("确认删除该项数据?", "警告", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      });

      await MenuAPI.deleteMenu([id]);
      await refreshAuthorizationUi();
      selectedRows.value = [];
      await loadMenuData();
    } catch {
      // 用户取消
    }
  }

  async function handleBatchDelete() {
    const ids = selectedIds.value;
    if (ids.length === 0) return;
    try {
      await ElMessageBox.confirm(`确定删除选中的 ${ids.length} 条数据吗？`, "批量删除", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      });
      batchDeleting.value = true;
      await MenuAPI.deleteMenu(ids);
      await refreshAuthorizationUi();
      selectedRows.value = [];
      await loadMenuData();
    } catch {
      // 用户取消
    } finally {
      batchDeleting.value = false;
    }
  }

  async function handleMoreClick(status: number) {
    const ids = selectedIds.value;
    if (!ids.length) {
      ElMessage.warning("请先选择要操作的数据");
      return;
    }
    try {
      await ElMessageBox.confirm(`确认${status === 0 ? "启用" : "停用"}该项数据?`, "警告", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      });
      moreLoading.value = true;
      await MenuAPI.batchMenu({ ids, status });
      await refreshAuthorizationUi();
      await loadMenuData();
    } catch {
      // 用户取消
    } finally {
      moreLoading.value = false;
    }
  }

  // ---- 新增 / 编辑 / 详情 ----
  async function resetForm() {
    if (dataFormRef.value) {
      dataFormRef.value.resetFields();
      dataFormRef.value.clearValidate();
    }
    Object.assign(formData.value, initialFormData);
  }

  async function handleCloseDialog() {
    dialogVisible.visible = false;
    createParentLocked.value = false;
    await resetForm();
  }

  async function handleAdd() {
    createLoading.value = true;
    try {
      await handleOpenDialog("create");
    } finally {
      createLoading.value = false;
    }
  }

  async function handleOpenDialog(
    type: "create" | "update" | "detail",
    id?: number,
    parentRow?: MenuTable
  ) {
    dialogVisible.type = type;
    createParentLocked.value = false;
    if (id) {
      const response = await MenuAPI.detailMenu(id);
      if (type === "detail") {
        dialogVisible.title = "菜单详情";
        Object.assign(detailFormData.value, response.data.data ?? {});
      } else if (type === "update") {
        dialogVisible.title = "修改菜单";
        Object.assign(formData.value, response.data.data ?? {});
      }
    } else {
      dialogVisible.title = "新增菜单";
      formData.value = { ...initialFormData };
      menuFormRenderKey.value += 1;
      if (parentRow?.id != null) {
        formData.value.parent_id = parentRow.id;
        if (parentRow.type === MenuTypeEnum.MENU) {
          createParentLocked.value = true;
          formData.value.type = MenuTypeEnum.BUTTON;
        } else if (parentRow.type === MenuTypeEnum.CATALOG) {
          formData.value.type = MenuTypeEnum.MENU;
        }
      }
    }
    refreshMenuOptions();
    dialogVisible.visible = true;
  }

  /**
   * 清理对新选中菜单类型无效的字段。
   *
   * @returns 无返回值；修改当前表单模型，避免将隐藏的旧值提交给严格校验的后端。
   */
  function handleMenuTypeChange() {
    const type = formData.value.type;
    if (type === MenuTypeEnum.CATALOG) {
      formData.value.permission = undefined;
      formData.value.component_path = undefined;
      formData.value.link = undefined;
      formData.value.is_iframe = false;
    } else if (type === MenuTypeEnum.MENU) {
      formData.value.component_path = "";
      formData.value.link = undefined;
      formData.value.is_iframe = false;
    } else if (type === MenuTypeEnum.BUTTON) {
      formData.value.route_name = undefined;
      formData.value.route_path = undefined;
      formData.value.component_path = undefined;
      formData.value.redirect = undefined;
      formData.value.link = undefined;
      formData.value.is_iframe = false;
    } else if (type === MenuTypeEnum.EXTLINK) {
      formData.value.permission = undefined;
      formData.value.component_path = undefined;
      formData.value.redirect = undefined;
    }
    nextTick(() => {
      dataFormRef.value?.clearValidate("redirect");
      if (formData.value.type === MenuTypeEnum.CATALOG) {
        dataFormRef.value?.validateField("redirect")?.catch(() => {});
      }
    });
  }

  async function handleSubmit() {
    if (submitLoading.value || !dataFormRef.value) return;
    const allowed = allowedMenuTypeValues.value;
    if (!allowed.length) return;
    submitLoading.value = true;
    try {
      const t = formData.value.type as MenuTypeEnum;
      if (!allowed.includes(t)) formData.value.type = allowed[0] as MenuForm["type"];
      const valid = await dataFormRef.value.validate()?.catch(() => false);
      if (!valid) return;
      const id = formData.value.id;
      if (id) {
        await MenuAPI.updateMenu(id, { id, ...formData.value });
      } else {
        await MenuAPI.createMenu(formData.value);
      }
      dialogVisible.visible = false;
      await resetForm();
      await loadMenuData();
      await refreshAuthorizationUi();
    } catch (error: unknown) {
      console.error(error);
    } finally {
      submitLoading.value = false;
    }
  }

  const opCtx: MenuRowActionContext = {
    onAdd: (r: MenuTable) => void handleOpenDialog("create", undefined, r),
    onDetail: (id: number) => void handleOpenDialog("detail", id),
    onEdit: (id: number) => void handleOpenDialog("update", id),
    onDelete: deleteMenuRow,
  };

  onMounted(() => {
    void loadMenuData();
  });

  return {
    // 搜索
    searchForm,
    showSearchBar,
    searchBarRef,
    searchBarRules,
    menuSearchItems,
    handleSearchBarSearch,
    onResetSearch,
    // 表格
    tableRef,
    tableData,
    loading,
    loadError,
    isExpanded,
    selectedIds,
    toggleExpand,
    loadMenuData,
    onTableSelectionChange,
    handleRowClick,
    // 工具栏
    batchDeleting,
    createLoading,
    moreLoading,
    submitLoading,
    handleAdd,
    handleBatchDelete,
    handleMoreClick,
    // 表单与抽屉
    formData,
    detailFormData,
    menuDialogFormItems,
    menuOptions,
    createParentLocked,
    dialogVisible,
    drawerSize,
    dataFormRef,
    menuFormRenderKey,
    rules,
    allowedMenuTypeValues,
    handleMenuTypeChange,
    handleOpenDialog,
    handleCloseDialog,
    handleSubmit,
    // 行内操作
    opCtx,
  };
}

export type MenuManagement = ReturnType<typeof useMenuManagement>;
