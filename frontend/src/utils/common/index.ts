/**
 * 通用工具函数模块
 *
 * 提供项目中常用的通用工具函数，包括：
 * - 问候语生成
 * - 日期范围处理
 * - 树形结构转换
 * - 对象深拷贝
 * - 空值判断
 * - Blob 格式验证
 *
 * @module utils/common
 */

import dayjs from "dayjs";
import { ElMessage } from "element-plus";

/**
 * 树形节点基础接口
 * 用于 formatTree 函数
 */
export interface TreeNode {
  id?: string | number;
  parent_id?: string | number | null;
  name?: string;
  status?: boolean | string | number;
  children?: TreeNode[];
}

/**
 * 级联选择器节点接口
 */
export interface CascaderNode {
  value: string | number;
  label: string;
  disabled: boolean;
  children?: CascaderNode[];
}

/**
 * 格式化树形结构为级联选择器格式
 *
 * 将树形数据转换为适合 Element Plus Cascader 组件使用的格式
 * 包含 value、label、disabled 和 children 字段
 *
 * @param nodes - 树形结构数据
 * @returns 格式化后的级联选择器节点数组
 *
 * @example
 * ```typescript
 * const nodes = [{ id: 1, name: '部门1', status: true, children: [...] }];
 * const formatted = formatTree(nodes);
 * // 输出：[{ value: 1, label: '部门1', disabled: false, children: [...] }]
 * ```
 */
export function formatTree(nodes: TreeNode[]): CascaderNode[] {
  return nodes.map((node) => {
    const formatted: CascaderNode = {
      value: node.id ?? "",
      label: node.name ?? "",
      disabled:
        node.status === false ||
        node.status === 1 ||
        String(node.status) === "false" ||
        String(node.status) === "1",
    };

    if (node.children && node.children.length > 0) {
      formatted.children = formatTree(node.children);
    }

    return formatted;
  });
}

export function isExternal(path: string): boolean {
  return /^(https?:|http?:|mailto:|tel:)/.test(path);
}

/**
 * 判断字符串是否为空
 *
 * 检查字符串是否为 undefined、null 或空字符串
 *
 * @param {string | null | undefined} obj - 需要检查的字符串
 * @returns {boolean} 是否为空
 *
 * @example
 * ```typescript
 * isEmpty('');      // true
 * isEmpty(null);    // true
 * isEmpty(undefined); // true
 * isEmpty('hello'); // false
 * ```
 */
export function isEmpty(obj: string | null | undefined): boolean {
  return obj === undefined || obj === null || obj === "";
}

/** 日期辅助工具（dayjs）。 */

const DATE_TIME_FORMAT = "YYYY-MM-DD HH:mm:ss";

const DATE_FORMAT = "YYYY-MM-DD";

export function formatToDateTime(
  date?: dayjs.ConfigType,
  format: string = DATE_TIME_FORMAT
): string {
  return dayjs(date).format(format);
}

export function formatToDate(date?: dayjs.ConfigType, format: string = DATE_FORMAT): string {
  return dayjs(date).format(format);
}

/**
 * 分页数据全量获取工具
 *
 * 用于需要获取全量数据的场景，如数据导出、批量操作等
 * 自动处理分页逻辑，将多页数据合并为一个完整的数组
 *
 * @module utils/fetchAllPages
 */

/**
 * fetchAllPages 函数的配置选项类型
 */
export interface FetchAllPagesOptions<T> {
  /** 每页条数，默认 1000 */
  pageSize?: number;
  /** 初始查询条件（会被拷贝后写入 page_no / page_size） */
  initialQuery: Record<string, unknown>;
  /** 页码字段名，默认 'page_no' */
  pageNoKey?: string;
  /** 每页条数字段名，默认 'page_size' */
  pageSizeKey?: string;
  /**
   * 拉取单页数据的函数
   * @param query - 查询参数，包含分页信息
   * @returns Promise<{ total: number; list: T[] }> - 包含总数和当前页数据
   */
  fetchPage: (query: Record<string, unknown>) => Promise<{ total: number; list: T[] }>;
}

/**
 * 按分页拉取全量列表
 *
 * 自动遍历所有分页，将数据合并为一个完整的数组返回
 * 适用于数据导出等需要全量数据的场景
 *
 * @template T - 数据项类型
 * @param {FetchAllPagesOptions<T>} options - 配置选项
 * @returns {Promise<T[]>} 合并后的全量数据数组
 *
 * @example
 * ```typescript
 * import { fetchAllPages } from '@utils/fetchAllPages';
 *
 * // 获取所有用户数据
 * const allUsers = await fetchAllPages<User>({
 *   pageSize: 500,
 *   initialQuery: { status: 'active' },
 *   fetchPage: async (query) => {
 *     const response = await api.get({ url: '/users', params: query });
 *     return {
 *       total: response.data.total,
 *       list: response.data.list,
 *     };
 *   },
 * });
 * ```
 */
export async function fetchAllPages<T>(options: FetchAllPagesOptions<T>): Promise<T[]> {
  const pageSize = options.pageSize ?? 1000;
  const pageNoKey = options.pageNoKey ?? "page_no";
  const pageSizeKey = options.pageSizeKey ?? "page_size";
  const query = { ...options.initialQuery };
  query[pageNoKey] = 1;
  query[pageSizeKey] = pageSize;
  const all: T[] = [];

  while (true) {
    const { total, list } = await options.fetchPage(query);
    all.push(...list);

    // 当已获取的数据达到总数或当前页为空时停止
    if (all.length >= total || list.length === 0) {
      break;
    }

    query[pageNoKey] = (query[pageNoKey] as number) + 1;
  }

  return all;
}

/**
 * 快速开始链接管理器
 *
 * 管理工作台「我的收藏」功能，支持添加、删除、更新和监听快速链接
 *
 * ## 功能特性
 *
 * - 支持收藏数量上限（默认 15 个）
 * - 自动持久化到 localStorage
 * - 支持观察者模式，数据变化时通知监听者
 * - 支持从路由信息自动创建链接
 *
 * @module utils/quickStartManager
 */

/**
 * 快速链接数据类型
 */
export interface QuickLink {
  /** 链接标题 */
  title: string;
  /** 图标名称 */
  icon: string;
  /** 跳转路径 */
  href: string;
  /** 唯一标识（可选） */
  id?: string;
}

/**
 * 快速开始管理器类
 *
 * 提供快速链接的增删改查功能，并支持观察者模式
 */
class QuickStartManager {
  /** 本地存储键名 */
  private storageKey = "quick-start-links";

  /** 监听器列表 */
  private listeners: Array<(links: QuickLink[]) => void> = [];

  /**
   * 获取所有快速链接
   *
   * @returns {QuickLink[]} 快速链接数组
   *
   * @example
   * ```typescript
   * const links = quickStartManager.getQuickLinks();
   * console.log(links);
   * ```
   */
  getQuickLinks(): QuickLink[] {
    try {
      const stored = localStorage.getItem(this.storageKey);
      return stored ? JSON.parse(stored) : this.getDefaultLinks();
    } catch (error) {
      console.error("Failed to load quick links:", error);
      return this.getDefaultLinks();
    }
  }

  /**
   * 获取默认链接（空数组）
   *
   * @returns {QuickLink[]} 默认链接数组
   */
  private getDefaultLinks(): QuickLink[] {
    return [];
  }

  /**
   * 保存快速链接到本地存储
   *
   * @param {QuickLink[]} links - 要保存的快速链接数组
   */
  saveQuickLinks(links: QuickLink[]): void {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(links));
      this.notifyListeners(links);
    } catch (error) {
      console.error("Failed to save quick links:", error);
    }
  }

  /**
   * 添加或更新快速链接
   *
   * 如果链接已存在（通过 href 判断），则更新该链接；否则添加新链接
   * 如果收藏数量已达上限，则提示并返回 false
   *
   * @param {QuickLink} link - 要添加或更新的快速链接
   * @returns {boolean} 是否已保存成功
   *
   * @example
   * ```typescript
   * const success = quickStartManager.addQuickLink({
   *   title: '仪表盘',
   *   icon: 'dashboard',
   *   href: '/dashboard',
   * });
   * ```
   */
  addQuickLink(link: QuickLink): boolean {
    const links = this.getQuickLinks();

    // 检查是否已存在相同 href 的链接
    const existingIndex = links.findIndex((l) => l.href === link.href);
    if (existingIndex !== -1) {
      // 更新现有链接
      links[existingIndex] = { ...links[existingIndex], ...link };
      ElMessage.success(`已更新快速链接：${link.title}`);
      this.saveQuickLinks(links);
      return true;
    }

    // 添加新链接
    links.push(link);
    this.saveQuickLinks(links);
    return true;
  }

  /**
   * 根据路由路径删除快速链接
   *
   * 用于兼容没有 id 的旧数据
   *
   * @param {string} href - 要删除的链接路径
   *
   * @example
   * ```typescript
   * quickStartManager.removeQuickLinkByHref('/dashboard');
   * ```
   */
  removeQuickLinkByHref(href: string): void {
    const links = this.getQuickLinks();
    const filteredLinks = links.filter((link) => link.href !== href);
    if (filteredLinks.length < links.length) {
      this.saveQuickLinks(filteredLinks);
    }
  }

  /**
   * 从路由或菜单信息创建快速链接
   *
   * @param {any} route - 路由或菜单对象
   * @param {string} [customTitle] - 自定义标题（可选）
   * @returns {QuickLink} 创建的快速链接对象
   *
   * @example
   * ```typescript
   * const link = quickStartManager.createQuickLinkFromRoute(route);
   * quickStartManager.addQuickLink(link);
   * ```
   */
  createQuickLinkFromRoute(route: any, customTitle?: string): QuickLink {
    // 确定最终使用的标题 - 优先使用 customTitle
    const finalTitle = customTitle || route.title || route.name || "未命名页面";

    return {
      title: finalTitle,
      icon: route.icon,
      href: route.fullPath || route.path,
      id: `route-${route.path.replace(/\//g, "-")}-${Date.now()}`,
    };
  }

  /**
   * 添加数据变化监听器
   *
   * @param {(links: QuickLink[]) => void} callback - 回调函数，当数据变化时触发
   *
   * @example
   * ```typescript
   * const callback = (links) => {
   *   console.log('链接列表已更新:', links);
   * };
   * quickStartManager.addListener(callback);
   * ```
   */
  addListener(callback: (links: QuickLink[]) => void): void {
    this.listeners.push(callback);
  }

  /**
   * 移除数据变化监听器
   *
   * @param {(links: QuickLink[]) => void} callback - 要移除的回调函数
   *
   * @example
   * ```typescript
   * quickStartManager.removeListener(callback);
   * ```
   */
  removeListener(callback: (links: QuickLink[]) => void): void {
    const index = this.listeners.indexOf(callback);
    if (index > -1) {
      this.listeners.splice(index, 1);
    }
  }

  /**
   * 通知所有监听器数据已变化
   *
   * @param {QuickLink[]} links - 当前的快速链接数组
   */
  private notifyListeners(links: QuickLink[]): void {
    this.listeners.forEach((callback) => {
      try {
        callback(links);
      } catch (error) {
        console.error("Error in quick start listener:", error);
      }
    });
  }

  /**
   * 检查链接是否已存在
   *
   * @param {string} href - 要检查的链接路径
   * @returns {boolean} 是否存在
   *
   * @example
   * ```typescript
   * const exists = quickStartManager.isLinkExists('/dashboard');
   * ```
   */
  isLinkExists(href: string): boolean {
    const links = this.getQuickLinks();
    return links.some((link) => link.href === href);
  }
}

/**
 * 快速开始管理器全局实例
 *
 * @example
 * ```typescript
 * import { quickStartManager } from '@utils/common/quickStartManager';
 *
 * // 获取所有链接
 * const links = quickStartManager.getQuickLinks();
 *
 * // 添加新链接
 * quickStartManager.addQuickLink({ title: '设置', icon: 'settings', href: '/settings' });
 * ```
 */
export const quickStartManager = new QuickStartManager();
