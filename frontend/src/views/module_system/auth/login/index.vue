<!-- 登录页：顶栏固定；仅插画列与表单区随布局切换 -->
<template>
  <div class="login-page-root flex w-full flex-col overflow-hidden">
    <div
      class="ambient-canvas pointer-events-none fixed inset-0 z-0 overflow-hidden"
      aria-hidden="true"
    >
      <div class="ambient-orb orb-1" />
      <div class="ambient-orb orb-2" />
      <div class="ambient-orb orb-3" />
      <div class="ambient-grid" />
    </div>
    <FaLoginCenterBackdrop v-if="panelAlign === 'center'" viewport-fixed />
    <FaAuthTopBar v-model:panel-align="panelAlign" />

    <div
      class="login-auth-split relative z-1 flex min-h-0 flex-1 overflow-hidden"
      :class="`login-auth-split--${panelAlign}`"
    >
      <div
        v-if="panelAlign !== 'center'"
        class="login-auth-split__col login-auth-split__col--illustration"
      >
        <FaEnterpriseIntro />
      </div>

      <div
        class="login-auth-split__col login-auth-split__col--form login-page-panel relative flex min-h-0 min-w-0 flex-col"
        :class="panelAlign === 'center' ? 'bg-transparent' : 'bg-transparent'"
      >
        <div class="login-page-panel__main relative z-1 flex min-h-0 flex-1 flex-col">
          <main class="auth-right-wrap" aria-labelledby="login-heading">
            <div class="form">
              <div class="form-intro">
                <div class="login-form-mark" aria-hidden="true">
                  <FaSvgIcon icon="ri:door-open-line" />
                </div>
                <h1 id="login-heading" class="title">{{ panelTitle }}</h1>
                <p class="sub-title">{{ panelSubTitle }}</p>
              </div>

              <FaLoginAccountForm
                ref="accountFormRef"
                v-model:login-form="loginForm"
                :rules="rules"
                :form-key="formKey"
                :loading="loading"
                :captcha-enabled="captchaEnabled"
                :captcha-image="captchaImage"
                :captcha-loading="captchaLoading"
                @submit="handleSubmit"
                @refresh-captcha="loadCaptcha(true)"
              />
              <p v-if="loginError" class="auth-feedback" role="alert">{{ loginError }}</p>
              <div v-if="captchaError" class="auth-feedback" role="alert">
                <p>{{ captchaError }}</p>
                <ElButton
                  link
                  type="primary"
                  :loading="captchaLoading"
                  @click="loadCaptcha(captchaEnabled)"
                  >重新获取验证码</ElButton
                >
              </div>
            </div>
          </main>
        </div>

        <footer class="login-page-footer login-page-footer--pinned shrink-0">
          <p class="login-footer-text">登录遇到问题，请联系管理员确认账号状态与访问权限。</p>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { LocationQuery, RouteLocationRaw } from "vue-router";
import type { LoginFormData } from "@/api/module_system/auth";
import AuthAPI from "@/api/module_system/auth";
import { useAppStore, useSettingsStore, useUserStore } from "@stores";
import { Auth, HttpError } from "@utils";
import { ElNotification, type FormRules } from "element-plus";
import FaLoginAccountForm from "@/components/views/fa-login/forms/FaLoginAccountForm.vue";
import FaAuthTopBar from "@/components/views/fa-login/widgets/FaAuthTopBar.vue";
import FaEnterpriseIntro from "@/components/views/fa-login/widgets/FaEnterpriseIntro.vue";
import { useLoginPanelAlign } from "@/components/views/fa-login/composables/useLoginPanelAlign";

defineOptions({ name: "Login" });

const settingStore = useSettingsStore();
const appStore = useAppStore();
const { t, locale } = useI18n();

const { panelAlign } = useLoginPanelAlign();

const panelTitle = computed(() => t("login.title"));
const panelSubTitle = computed(() => t("login.subTitle"));

const formKey = ref(0);

watch(locale, () => {
  formKey.value++;
});

const userStore = useUserStore();
const router = useRouter();
const route = useRoute();

const accountFormRef = ref<InstanceType<typeof FaLoginAccountForm> | null>(null);
const loading = ref(false);
const loginError = ref("");

const loginForm = reactive<LoginFormData>({
  username: "",
  password: "",
  remember: true,
  login_type: "PC",
  captcha_key: "",
  captcha: "",
});

const captchaEnabled = ref(false);
const captchaImage = ref("");
const captchaLoading = ref(false);
const captchaError = ref("");

/** 加载新的登录验证挑战，并按需显示自适应 CAPTCHA。 */
async function loadCaptcha(forceVisible = false) {
  captchaLoading.value = true;
  captchaError.value = "";
  try {
    const response = await AuthAPI.getCaptcha();
    const data = response.data.data;
    captchaImage.value = data?.img_base || "";
    loginForm.captcha_key = data?.key || "";
    loginForm.captcha = "";
    captchaEnabled.value = forceVisible || Boolean(data?.enable);
  } catch (error) {
    captchaError.value = "验证码加载失败，请重新获取后再登录。";
    console.warn("[Login] 获取验证码失败", error);
    if (forceVisible) captchaEnabled.value = true;
  } finally {
    captchaLoading.value = false;
  }
}

const rules = computed<FormRules>(() => {
  return {
    username: [
      {
        required: true,
        trigger: "blur",
        message: t("login.message.username.required"),
      },
    ],
    password: [
      {
        required: true,
        trigger: "blur",
        message: t("login.message.password.required"),
      },
      {
        min: 6,
        message: t("login.message.password.min"),
        trigger: "blur",
      },
    ],
    captcha: [
      {
        required: captchaEnabled.value,
        trigger: "blur",
        message: "请输入验证码",
      },
    ],
  };
});

function resolveRedirectTarget(query: LocationQuery): RouteLocationRaw {
  const defaultPath = "/";
  const rawRedirect = typeof query.redirect === "string" ? query.redirect : defaultPath;
  if (!rawRedirect.startsWith("/") || rawRedirect.startsWith("//") || rawRedirect.includes("://")) {
    return { path: defaultPath };
  }
  try {
    const resolved = router.resolve(rawRedirect);
    return {
      path: resolved.path,
      query: resolved.query,
    };
  } catch {
    return { path: defaultPath };
  }
}

async function consumeOAuthTicket(): Promise<boolean> {
  const ticket = typeof route.query.oauth_ticket === "string" ? route.query.oauth_ticket : "";
  if (!ticket) return false;

  const response = await AuthAPI.exchangeOAuthTicket({ ticket });
  const data = response.data.data;
  if (!data?.access_token || !data.refresh_token) {
    throw new Error("OAuth 登录凭证无效");
  }
  Auth.setTokens(data.access_token, data.refresh_token, true);
  userStore.setToken(data.access_token, data.refresh_token);
  await userStore.getUserInfo();
  userStore.setLoginStatus(true);
  const cleanQuery = { ...route.query };
  delete cleanQuery.oauth_ticket;
  await router.replace(resolveRedirectTarget(cleanQuery));
  return true;
}

onMounted(async () => {
  const oauthError = route.query.oauth_error;
  if (typeof oauthError === "string" && oauthError) loginError.value = oauthError;
  try {
    if (await consumeOAuthTicket()) return;
    await loadCaptcha();
  } catch (error) {
    loginError.value = "第三方登录未完成，请重试或使用账号密码登录。";
    console.warn("[Login] 登录初始化失败，继续使用默认渲染", error);
    ElNotification({
      title: "登录失败",
      message: error instanceof Error ? error.message : "OAuth 登录凭证无效或已过期",
      type: "error",
    });
  }
  if (userStore.isLogin) {
    await router.replace(resolveRedirectTarget(route.query));
    return;
  }
});

const handleSubmit = async () => {
  if (!accountFormRef.value || loading.value) return;
  loading.value = true;
  loginError.value = "";

  try {
    const valid = await accountFormRef.value.validate?.();
    if (!valid) return;

    await userStore.login(loginForm);
    await router.replace(resolveRedirectTarget(route.query));

    if (settingStore.showGuide) {
      appStore.showGuide(true);
    }
  } catch (error) {
    loginError.value = error instanceof HttpError ? error.message : "登录失败，请稍后重试。";
    if (error instanceof HttpError && error.message.includes("验证码")) {
      await loadCaptcha(true);
    }
    if (!(error instanceof HttpError)) {
      console.error("[Login] Unexpected error:", error);
      ElNotification({
        title: "提示",
        message: error instanceof Error ? error.message : String(error),
        type: "error",
      });
    }
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped lang="scss">
@use "../../../../components/views/fa-login/fa-login";
</style>
