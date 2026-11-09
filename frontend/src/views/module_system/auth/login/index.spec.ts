import { flushPromises, mount } from "@vue/test-utils";
import { computed, defineComponent, onMounted, reactive, ref, watch } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Login from "./index.vue";

const { login, validate, replace, notification, getCaptcha } = vi.hoisted(() => ({
  login: vi.fn(),
  validate: vi.fn(),
  replace: vi.fn(),
  notification: vi.fn(),
  getCaptcha: vi.fn(),
}));
vi.mock("@stores", () => ({
  useUserStore: () => ({ login, isLogin: false }),
  useSettingsStore: () => ({ showGuide: false }),
  useAppStore: () => ({ showGuide: vi.fn() }),
}));
vi.mock("@/api/module_system/auth", () => ({
  default: { getCaptcha },
}));
vi.mock("@utils", () => ({ Auth: {}, HttpError: class extends Error {} }));

const routeState = { query: {} as Record<string, unknown> };
vi.mock("element-plus", () => ({ ElNotification: notification }));
vi.mock("@/components/views/fa-login/widgets/FaAuthTopBar.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/views/fa-login/widgets/FaEnterpriseIntro.vue", () => ({
  default: { template: "<div />" },
}));

Object.assign(globalThis, {
  computed,
  onMounted,
  reactive,
  ref,
  watch,
  useI18n: () => ({ t: (key: string) => key, locale: ref("zh") }),
  useRouter: () => ({ replace, resolve: (path: string) => ({ path, query: {} }) }),
  useRoute: () => routeState,
});

const AccountForm = defineComponent({
  name: "FaLoginAccountForm",
  setup(_props, { expose }) {
    expose({ validate });
  },
  template: "<button @click=\"$emit('submit')\">登录</button>",
});

describe("login submission locking", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeState.query = {};
    login.mockResolvedValue(undefined);
    validate.mockResolvedValue(true);
    replace.mockResolvedValue(undefined);
    getCaptcha.mockResolvedValue({ data: { data: { enable: false } } });
  });

  function renderLogin() {
    return mount(Login, {
      global: {
        stubs: {
          FaLoginAccountForm: AccountForm,
          FaLoginCenterBackdrop: true,
          ElScrollbar: { template: "<div><slot /></div>" },
          ElButton: {
            emits: ["click"],
            template: "<button @click=\"$emit('click')\"><slot /></button>",
          },
        },
      },
    });
  }

  it("locks before validation and keeps the lock until login resolves", async () => {
    const validation = Promise.withResolvers<boolean>();
    const authentication = Promise.withResolvers<void>();
    validate.mockReturnValue(validation.promise);
    login.mockReturnValue(authentication.promise);
    const wrapper = renderLogin();
    await flushPromises();
    await wrapper.get("button").trigger("click");
    await wrapper.get("button").trigger("click");
    expect(validate).toHaveBeenCalledOnce();
    expect(login).not.toHaveBeenCalled();
    validation.resolve(true);
    await flushPromises();
    await wrapper.get("button").trigger("click");
    expect(login).toHaveBeenCalledOnce();
    authentication.resolve();
    await flushPromises();
    expect(replace).toHaveBeenCalledOnce();
    wrapper.unmount();
  });

  it("releases the lock after failed validation or a failed login", async () => {
    const wrapper = renderLogin();
    await flushPromises();
    validate.mockResolvedValueOnce(false);
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(login).not.toHaveBeenCalled();
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    login.mockRejectedValueOnce(new Error("登录失败"));
    await wrapper.get("button").trigger("click");
    await flushPromises();
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(login).toHaveBeenCalledTimes(2);
    expect(replace).toHaveBeenCalledOnce();
    log.mockRestore();
    wrapper.unmount();
  });

  it("keeps a visible error next to the form and clears it when retrying", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    login.mockRejectedValueOnce(new Error("unavailable"));
    const wrapper = renderLogin();
    await flushPromises();
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("登录失败");
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(login).toHaveBeenCalledTimes(2);
    log.mockRestore();
    wrapper.unmount();
  });

  it("offers captcha recovery instead of leaving a failed challenge silent", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(() => {});
    getCaptcha.mockRejectedValueOnce(new Error("unavailable"));
    const wrapper = renderLogin();
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("验证码加载失败");
    await wrapper.get('[role="alert"] button').trigger("click");
    await flushPromises();
    expect(getCaptcha).toHaveBeenCalledTimes(2);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    log.mockRestore();
    wrapper.unmount();
  });

  it("hides third-party login and shows a returned provider error", async () => {
    routeState.query = {
      oauth_error:
        "github OAuth \u672a\u914d\u7f6e\uff08\u5ba2\u6237\u7aef\u5bc6\u94a5\u4e3a\u7a7a\uff09",
    };
    const wrapper = renderLogin();
    await flushPromises();
    expect(wrapper.findComponent({ name: "FaLoginThirdPartySection" }).exists()).toBe(false);
    expect(wrapper.get('[role="alert"]').text()).toContain("\u672a\u914d\u7f6e");
    wrapper.unmount();
  });
});
