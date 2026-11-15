import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "./index.vue";
import HealthAPI from "@/api/module_common/health";
import AiChatAPI from "@/api/module_ai/chat";
import KnowledgeAPI from "@/api/module_ai/knowledge";
import type { AppRouteRecord } from "@/types/router";

const stores = vi.hoisted(() => ({
  menu: { menuList: [] as AppRouteRecord[] },
  user: { basicInfo: { name: "测试用户", is_superuser: false }, getPerms: [] as string[] },
}));

vi.mock("@stores", () => ({ useMenuStore: () => stores.menu, useUserStore: () => stores.user }));
vi.mock("@utils", () => ({
  HttpError: class extends Error {},
  formatMenuTitle: (title: string) => title,
}));
vi.mock("@/api/module_common/health", () => ({ default: { getReadiness: vi.fn() } }));
vi.mock("@/api/module_ai/chat", () => ({ default: { getUsage: vi.fn() } }));
vi.mock("@/api/module_ai/knowledge", () => ({ default: { getStats: vi.fn() } }));
vi.mock("./modules/login-trend.vue", () => ({
  default: { name: "LoginTrend", template: '<section data-test="login-trend" />' },
}));

let wrapper: VueWrapper | undefined;
const route = (path: string, title = path, meta = {}): AppRouteRecord => ({
  path,
  component: "/page/index",
  meta: { title, ...meta },
});
const createPage = () => {
  wrapper = mount(Home, {
    global: {
      mocks: {
        $t: (text: string) => {
          if (!text.includes(".")) throw new Error(`Literal menu title translated: ${text}`);
          return text;
        },
      },
      stubs: {
        FaSvgIcon: true,
        RouterLink: { props: ["to"], template: '<a :href="to"><slot /></a>' },
      },
    },
  });
  return wrapper;
};

beforeEach(() => {
  stores.menu.menuList = [];
  stores.user.basicInfo.is_superuser = false;
  stores.user.getPerms = [];
  vi.mocked(HealthAPI.getReadiness).mockResolvedValue({
    data: {
      data: {
        status: 1,
        disk_usage: 12,
        dependencies: {
          database: { status: 1, enabled: true, latency_ms: 1 },
          redis: { status: 1, enabled: true, latency_ms: 1 },
        },
      },
    },
  } as Awaited<ReturnType<typeof HealthAPI.getReadiness>>);
  vi.mocked(KnowledgeAPI.getStats).mockRejectedValue(new Error("no permission"));
  vi.mocked(AiChatAPI.getUsage).mockRejectedValue(new Error("no permission"));
});
afterEach(() => wrapper?.unmount());

describe("首页工作入口", () => {
  it("从授权菜单提取知识入口，不再被前八项截断或重复展示", async () => {
    stores.menu.menuList = [
      ...Array.from({ length: 8 }, (_, index) => route(`/system/page-${index}`)),
      route("/ai/chat", "AI 对话"),
      route("/ai/knowledge", "知识库管理"),
      route("/ai/document", "文档管理"),
    ];
    const page = createPage();
    await flushPromises();
    expect(page.find(".knowledge-launch__action").attributes("href")).toBe("/ai/chat");
    expect(page.findAll(".knowledge-launch__resources a")).toHaveLength(3);
    expect(page.find(".pending-panel").exists()).toBe(false);
    expect(page.findAll('a[href="/ai/knowledge"]')).toHaveLength(1);
    expect(page.find(".home-health--healthy").text()).toContain("基础服务正常");
  });

  it("不显示隐藏、外链、参数页面和未授权的 AI 或登录统计", () => {
    stores.menu.menuList = [
      route("/ai/chat", "对话", { hidden: true }),
      route("/ai/knowledge", "知识库", { isHide: true }),
      route("/external", "外链", { link: "https://example.com" }),
      route("//example.com", "外部网站"),
      route("/item/:id", "详情"),
      route("/home", "首页"),
      route("/system/user", "用户管理"),
    ];
    const page = createPage();
    expect(page.find(".knowledge-launch").exists()).toBe(false);
    expect(page.find('[data-test="login-trend"]').exists()).toBe(false);
    expect(page.find(".pending-panel").exists()).toBe(false);
  });

  it("有登录日志权限才呈现统计，知识入口只有一项时仍可访问", () => {
    stores.user.getPerms = ["module_system:login_log:query"];
    stores.menu.menuList = [route("/ai/document", "文档管理")];
    const page = createPage();
    expect(page.find('[data-test="login-trend"]').exists()).toBe(true);
    expect(page.find(".knowledge-launch__action").exists()).toBe(false);
    expect(page.find(".knowledge-launch__resources a").attributes("href")).toBe("/ai/document");
  });

  it("按接口数据呈现业务指标，并在有待处理事项时显示待处理面板", async () => {
    stores.menu.menuList = [route("/ai/chat", "AI 对话"), route("/ai/document", "文档管理")];
    vi.mocked(KnowledgeAPI.getStats).mockResolvedValue({
      data: {
        data: {
          knowledge_bases: 2,
          documents: 12,
          indexed_documents: 9,
          processing_documents: 1,
          failed_documents: 2,
        },
      },
    } as Awaited<ReturnType<typeof KnowledgeAPI.getStats>>);
    vi.mocked(AiChatAPI.getUsage).mockResolvedValue({
      data: {
        data: {
          user_id: 1,
          day: "2026-11-15",
          timezone: "Asia/Shanghai",
          calls: 7,
          daily_limit: 20,
          remaining: 13,
        },
      },
    } as Awaited<ReturnType<typeof AiChatAPI.getUsage>>);
    const page = createPage();
    await flushPromises();
    expect(page.findAll(".workspace-metric")).toHaveLength(4);
    expect(page.find(".workspace-metrics").text()).toContain("剩余 13 / 20");
    expect(page.findAll(".pending-panel a").map((link) => link.attributes("href"))).toEqual([
      "/ai/document",
      "/ai/document",
    ]);
    expect(page.find(".pending-panel").text()).toContain("文档解析失败");
  });

  it("没有可用权限时保留明确空态，状态请求失败后可以重试", async () => {
    vi.mocked(HealthAPI.getReadiness).mockRejectedValueOnce(new Error("offline"));
    const page = createPage();
    await flushPromises();
    expect(page.find(".home-empty").text()).toContain("联系管理员");
    expect(page.find(".home-health--unavailable").text()).toContain("暂时无法检查");
    await page.get('[aria-label^="刷新基础服务状态"]').trigger("click");
    await flushPromises();
    expect(page.find(".home-health--healthy").exists()).toBe(true);
  });
});
