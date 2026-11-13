/**
 * 路由转换器 —— 菜单树 → vue-router 记录。
 * 处理 iframe、一级叶子路由、普通路由三种形态，支持 shellChild 模式。
 * 目录只保留 children，不挂中间层组件；RouterView 会直接渲染叶子页面。
 */
import type { RouteRecordRaw } from "vue-router";
import type { AppRouteRecord } from "@/types/router";
import { ComponentLoader } from "./ComponentLoader";
import { IframeRouteManager } from "../staticRoutes";

interface ConvertedRoute extends Omit<RouteRecordRaw, "children"> {
  id?: number;
  children?: ConvertedRoute[];
  component?: RouteRecordRaw["component"] | (() => Promise<any>);
}

export class RouteTransformer {
  private componentLoader: ComponentLoader;
  private iframeManager: IframeRouteManager;
  private readonly shellChild: boolean;

  constructor(componentLoader: ComponentLoader, options?: { shellChild?: boolean }) {
    this.componentLoader = componentLoader;
    this.shellChild = options?.shellChild ?? false;
    this.iframeManager = IframeRouteManager.getInstance();
  }

  transform(route: AppRouteRecord, depth = 0, parentAbsPath = ""): ConvertedRoute {
    const absPath = (route.path || "").trim();
    const pathOut = this.routerPath(depth, parentAbsPath, absPath);
    const { component, children, ...routeConfig } = route;

    const converted: ConvertedRoute = { ...routeConfig, path: pathOut, component: undefined };

    if (route.meta.isIframe) {
      this.handleIframeRoute(converted, route);
    } else if (!children?.length) {
      this.handleLeafRoute(converted, route, component as string, depth);
    }

    if (children?.length) {
      converted.children = children.map((c) => this.transform(c, depth + 1, absPath));
    }

    return converted;
  }

  private routerPath(depth: number, parentAbsPath: string, absPath: string): string {
    if (!this.shellChild) {
      return absPath;
    }
    if (depth === 0) {
      return absPath.replace(/^\/+/, "");
    }
    if (!parentAbsPath || !absPath) return absPath;
    const p = parentAbsPath.replace(/\/$/, "");
    if (absPath.startsWith(`${p}/`)) return absPath.slice(p.length + 1);
    return absPath.split("/").filter(Boolean).pop() ?? absPath;
  }

  private handleIframeRoute(targetRoute: ConvertedRoute, sourceRoute: AppRouteRecord): void {
    targetRoute.component = this.componentLoader.loadIframe();
    this.iframeManager.add(sourceRoute);
  }

  private handleLeafRoute(
    converted: ConvertedRoute,
    route: AppRouteRecord,
    component: string | undefined,
    depth: number
  ): void {
    converted.component = component ? this.componentLoader.load(component) : undefined;
    if (depth === 0) route.meta.isFirstLevel = true;
  }
}
