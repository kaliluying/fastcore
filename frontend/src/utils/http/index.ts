/** HTTP 工具：Axios 实例、共享常量、错误类型和拦截器。 */

import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";
import qs from "qs";
import { ElMessage } from "element-plus";
import { ResultEnum } from "@/enums/api/result.enum";
import { Auth } from "@/utils/auth";
import { redirectToLogin } from "@/utils/auth";
import { $t } from "@/locales";

declare module "axios" {
  interface AxiosRequestConfig {
    showErrorMessage?: boolean;
    showSuccessMessage?: boolean;
    authSessionKey?: string;
    authRetried?: boolean;
  }
}

// --- 配置常量 ---------------------------------------------------

/** 跳过认证的标记：无需携带令牌时，将 headers.Authorization 设为此值。 */
export const NO_AUTH_FLAG = "no-auth";

export interface ExtendedRequestConfig extends AxiosRequestConfig {
  skipAuth?: boolean;
}

// --- HTTP 状态码与 HttpError ----------------------------------

export enum ApiStatus {
  success = 200,
  error = 400,
  unauthorized = 401,
  forbidden = 403,
  notFound = 404,
  methodNotAllowed = 405,
  requestTimeout = 408,
  internalServerError = 500,
  badGateway = 502,
  serviceUnavailable = 503,
  gatewayTimeout = 504,
}

export interface ErrorLogData {
  code: number;
  message: string;
  data?: unknown;
  timestamp: string;
  url?: string;
  method?: string;
  stack?: string;
}

export class HttpError extends Error {
  public readonly code: number;
  public readonly data?: unknown;
  public readonly timestamp: string;
  public readonly url?: string;
  public readonly method?: string;

  constructor(
    message: string,
    code: number,
    options?: {
      data?: unknown;
      url?: string;
      method?: string;
    }
  ) {
    super(message);
    this.name = "HttpError";
    this.code = code;
    this.data = options?.data;
    this.timestamp = new Date().toISOString();
    this.url = options?.url;
    this.method = options?.method;
  }

  public toLogData(): ErrorLogData {
    return {
      code: this.code,
      message: this.message,
      data: this.data,
      timestamp: this.timestamp,
      url: this.url,
      method: this.method,
      stack: this.stack,
    };
  }
}

const getErrorMessage = (status: number): string => {
  const errorMap: Record<number, string> = {
    [ApiStatus.unauthorized]: "httpMsg.unauthorized",
    [ApiStatus.forbidden]: "httpMsg.forbidden",
    [ApiStatus.notFound]: "httpMsg.notFound",
    [ApiStatus.methodNotAllowed]: "httpMsg.methodNotAllowed",
    [ApiStatus.requestTimeout]: "httpMsg.requestTimeout",
    [ApiStatus.internalServerError]: "httpMsg.internalServerError",
    [ApiStatus.badGateway]: "httpMsg.badGateway",
    [ApiStatus.serviceUnavailable]: "httpMsg.serviceUnavailable",
    [ApiStatus.gatewayTimeout]: "httpMsg.gatewayTimeout",
  };

  return $t(errorMap[status] || "httpMsg.internalServerError");
};

export function handleError(error: AxiosError<ApiResponse>): never {
  if (error.code === "ERR_CANCELED") {
    console.warn("Request cancelled:", error.message);
    throw new HttpError($t("httpMsg.requestCancelled"), ApiStatus.error);
  }

  const statusCode = error.response?.status;
  const errorMessage = error.response?.data?.msg || error.message;
  const requestConfig = error.config;

  if (!error.response) {
    throw new HttpError($t("httpMsg.networkError"), ApiStatus.error, {
      url: requestConfig?.url,
      method: requestConfig?.method?.toUpperCase(),
    });
  }

  const message = statusCode
    ? getErrorMessage(statusCode)
    : errorMessage || $t("httpMsg.requestFailed");
  throw new HttpError(message, statusCode || ApiStatus.error, {
    data: error.response.data,
    url: requestConfig?.url,
    method: requestConfig?.method?.toUpperCase(),
  });
}

export function showError(error: HttpError, showMessage: boolean = true): void {
  if (showMessage) {
    ElMessage.error(error.message);
  }
  console.error("[HTTP Error]", error.toLogData());
}

export function showSuccess(message: string, showMessage: boolean = true): void {
  if (showMessage) {
    ElMessage.success(message);
  }
}

export const isHttpError = (error: unknown): error is HttpError => {
  return error instanceof HttpError;
};

/**
 * 判断 HTTP 请求失败时是否应显示全局错误消息。
 *
 * @param config 失败请求对应的配置。
 * @returns 是否显示统一的错误通知。
 */
function shouldShowErrorMessage(config?: AxiosRequestConfig): boolean {
  return config?.showErrorMessage !== false;
}

/**
 * 创建 HTTP 错误并保留服务端响应数据，供调用方在本地恢复。
 *
 * @param message 可读的错误消息。
 * @param code HTTP 状态码或业务错误码。
 * @param data 服务端返回的响应数据。
 * @param config 产生错误的请求配置。
 * @returns 供调用方使用的标准化错误对象。
 */
function createHttpError(
  message: string,
  code: number,
  data?: unknown,
  config?: AxiosRequestConfig
): HttpError {
  return new HttpError(message, code, {
    data,
    url: config?.url,
    method: config?.method?.toUpperCase(),
  });
}

// --- Axios 实例 ------------------------------------------------------------

export const request: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_APP_BASE_API,
  timeout: Number(import.meta.env.VITE_API_TIMEOUT) || 15000,
  headers: { "Content-Type": "application/json;charset=utf-8" },
  paramsSerializer: (params) => qs.stringify(params, { indices: false }),
});

request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const accessToken = Auth.getAccessToken();
    const auth = config.headers.Authorization;

    if (auth === NO_AUTH_FLAG) {
      delete config.headers.Authorization;
      return config;
    }

    if (config.authSessionKey !== undefined && config.authSessionKey !== Auth.getSessionKey()) {
      throw new axios.CanceledError("登录会话已变更");
    }
    config.authSessionKey ??= Auth.getSessionKey();
    if (!auth && accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`;
    }

    return config;
  },
  (error) => {
    if (axios.isCancel(error)) throw error;
    const msg = error instanceof Error ? error.message : String(error);
    ElMessage.error(msg);
    throw error;
  },
  // 发起登出请求时立即绑定凭证，随后本地即可同步注销会话。
  { synchronous: true }
);

/**
 * 响应拦截器。
 *
 * 1. 成功响应：
 *    - Blob 响应直接透传，用于文件下载。
 *    - 业务状态码非成功时拒绝请求。
 *    - 非 GET 且非登录/登出的变更请求成功时，显示成功消息。
 *
 * 2. 未收到响应的网络错误：
 *    - 为连接被拒绝、超时和其他网络错误提供易于理解的消息。
 *
 * 3. 收到响应的业务或认证错误：
 *    - 尽可能将 Blob 错误解析为 JSON。
 *    - 401 / TOKEN_EXPIRED 触发静默刷新令牌并重放请求。
 *    - 其他业务错误按错误码映射。
 */
request.interceptors.response.use(
  (response: AxiosResponse<ApiResponse>) => {
    if (["blob", "stream"].includes(response.config.responseType || "")) {
      return response;
    }

    const data = response.data;

    if (data.code !== ResultEnum.SUCCESS) {
      if (shouldShowErrorMessage(response.config)) {
        ElMessage.error(data.msg);
      }
      return Promise.reject(
        createHttpError(data.msg || "请求失败", data.code, data, response.config)
      );
    }

    if (
      response.config.method?.toUpperCase() !== "GET" &&
      !response.config.url?.includes("login") &&
      !response.config.url?.includes("logout") &&
      response.config.showSuccessMessage !== false
    ) {
      ElMessage.success(data.msg);
    }

    return response;
  },
  async (error: AxiosError<ApiResponse>) => {
    if (axios.isCancel(error)) return Promise.reject(error);
    if (error.config?.signal?.aborted) return Promise.reject(error);
    if (error.response?.config.responseType === "stream") {
      try {
        error.response.data = await new Response(
          error.response.data as unknown as ReadableStream
        ).json();
      } catch {
        return Promise.reject(new Error("服务器未返回有效的错误信息"));
      }
    }
    const showErrorMessage = shouldShowErrorMessage(error.config);

    // 未收到响应体的网络错误。
    if (!error.response) {
      let errorMessage = "网络连接异常";

      if (error.message?.includes("ECONNREFUSED")) {
        errorMessage = "服务器连接失败，请检查后端服务是否正常运行";
      } else if (error.message?.includes("timeout")) {
        errorMessage = "请求超时，请稍后重试";
      } else if (error.message?.includes("Network Error")) {
        errorMessage = "网络连接错误，请检查网络设置";
      }

      console.error("网络请求失败:", error);
      if (showErrorMessage) ElMessage.error(errorMessage);
      return Promise.reject(new Error(errorMessage));
    }

    const data = error.response?.data;

    // Blob 错误响应，通常来自文件下载 API。
    if (error.response?.config.responseType === "blob" && error.response.data instanceof Blob) {
      try {
        const text = await new Response(error.response.data).text();
        const jsonData: ApiResponse = JSON.parse(text);

        if (jsonData.code === ResultEnum.ERROR) {
          if (showErrorMessage) ElMessage.error(jsonData.msg || "请求错误");
          return Promise.reject(new Error(jsonData.msg || "请求错误"));
        } else if (jsonData.code === ResultEnum.EXCEPTION) {
          if (showErrorMessage) ElMessage.error(jsonData.msg || "服务异常");
          return Promise.reject(new Error(jsonData.msg || "服务异常"));
        }
      } catch (e) {
        console.error("请求异常:", e);
        if (showErrorMessage) ElMessage.error("数据解析失败");
        return Promise.reject(new Error("数据解析失败"));
      }
    }

    // 认证错误：条件允许时静默刷新令牌。
    const status = error.response.status;

    const hasApiCode =
      data !== undefined &&
      data !== null &&
      typeof data === "object" &&
      "code" in data &&
      typeof (data as ApiResponse).code === "number";

    if ((status === 401 && !hasApiCode) || data?.code === ResultEnum.TOKEN_EXPIRED) {
      const config = error.config as InternalAxiosRequestConfig | undefined;

      // 刷新接口本身失败时，由下方的 catch 块统一处理一次跳转。
      // 避免重复发送通知。
      if (config?.url?.includes("auth/token/refresh")) {
        return Promise.reject(
          new HttpError(data?.msg || "刷新令牌已过期，请重新登录", ApiStatus.unauthorized)
        );
      }

      const sessionKey = config?.authSessionKey;
      if (!config || !sessionKey || sessionKey !== Auth.getSessionKey()) {
        return Promise.reject(new HttpError("登录会话已变更", ApiStatus.unauthorized));
      }

      const msg = data?.msg || "登录已失效，请重新登录";
      if (config.authRetried || config.url?.includes("auth/logout")) {
        await redirectToLogin(msg, sessionKey);
        return Promise.reject(new HttpError(msg, ApiStatus.unauthorized));
      }

      config.authRetried = true;
      try {
        // 同一会话中，迟到的旧 token 401 可直接使用已续期的 token。
        if (config.headers.Authorization === `Bearer ${Auth.getAccessToken()}`) {
          await Auth.refreshTokens(sessionKey);
        }
        if (sessionKey !== Auth.getSessionKey()) {
          throw new HttpError("登录会话已变更", ApiStatus.unauthorized);
        }
        config.headers.Authorization = `Bearer ${Auth.getAccessToken()}`;
        return request(config);
      } catch {
        await redirectToLogin(msg, sessionKey);
        return Promise.reject(new HttpError(msg, ApiStatus.unauthorized));
      }
    }

    // 按错误码映射业务错误。
    if (data?.code === ResultEnum.ERROR) {
      const message = data.msg || "请求错误";
      if (showErrorMessage) ElMessage.error(message);
      return Promise.reject(createHttpError(message, ApiStatus.error, data, error.config));
    } else if (data?.code === ResultEnum.UNAUTHORIZED) {
      const message = data.msg || "暂无权限";
      if (showErrorMessage) ElMessage.error(message);
      return Promise.reject(createHttpError(message, ApiStatus.unauthorized, data, error.config));
    } else if (data?.code === ResultEnum.EXCEPTION) {
      const message = data.msg || "服务异常";
      if (showErrorMessage) ElMessage.error(message);
      return Promise.reject(createHttpError(message, ApiStatus.error, data, error.config));
    } else {
      const message = data?.msg || "请求处理失败，请稍后重试";
      if (showErrorMessage) ElMessage.error(message);
      return Promise.reject(
        createHttpError(message, status || ApiStatus.error, data, error.config)
      );
    }
  }
);

export type { AxiosInstance } from "axios";
