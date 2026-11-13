/** 分页响应、错误和防抖工具；不依赖页面、路由或认证状态。 */

// --- 全局分页字段名（与 PageQuery 对齐） ---

/** 仅保留与全局 `PageQuery` / `PageResult` 对齐的分页参数字段名（供 useTable 合并请求参数） */
export const tableConfig = {
  paginationKey: {
    current: "page_no",
    size: "page_size",
  },
} as const;

/** 与 `global.d.ts` 中 `PageResult` 字段一致；分页列表接口必须返回该结构（或包在 ApiResponse.data 内） */
function isPageResultPayload(o: unknown): o is PageResult<unknown> {
  if (!o || typeof o !== "object" || Array.isArray(o)) return false;
  const r = o as Record<string, unknown>;
  return (
    Array.isArray(r.items) &&
    typeof r.total === "number" &&
    typeof r.page_no === "number" &&
    typeof r.page_size === "number" &&
    typeof r.has_next === "boolean"
  );
}

/** 将常见分页形状（含 MyBatis-Plus / 旧字段名）规范为全局 `PageResult` */
function normalizePageResultLike(raw: unknown): PageResult<unknown> | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;

  const items =
    (Array.isArray(o.items) && o.items) ||
    (Array.isArray(o.records) && o.records) ||
    (Array.isArray(o.list) && o.list) ||
    (Array.isArray(o.rows) && o.rows) ||
    null;
  if (!items) return null;

  const totalRaw = o.total;
  const total =
    typeof totalRaw === "number" ? totalRaw : typeof totalRaw === "string" ? Number(totalRaw) : NaN;
  if (!Number.isFinite(total)) return null;

  const pageNoRaw = o.page_no ?? o.current ?? o.page;
  const page_no =
    typeof pageNoRaw === "number"
      ? pageNoRaw
      : typeof pageNoRaw === "string"
        ? Number(pageNoRaw)
        : NaN;
  if (!Number.isFinite(page_no)) return null;

  const pageSizeRaw = o.page_size ?? o.size ?? o.limit ?? o.pageSize;
  const page_size =
    typeof pageSizeRaw === "number"
      ? pageSizeRaw
      : typeof pageSizeRaw === "string"
        ? Number(pageSizeRaw)
        : NaN;
  if (!Number.isFinite(page_size)) return null;

  let has_next: boolean;
  if (typeof o.has_next === "boolean") {
    has_next = o.has_next;
  } else if (typeof o.hasNext === "boolean") {
    has_next = o.hasNext;
  } else {
    has_next = page_no * page_size < total;
  }

  return { items, total, page_no, page_size, has_next };
}

/** useTable 内部使用的规范化分页响应（由 PageResult 映射而来） */
export interface ApiResponse<T = unknown> {
  records: T[];
  total: number;
  current?: number;
  size?: number;
  has_next?: boolean;
  [key: string]: unknown;
}

// --- 响应解析与防抖 ---

function unwrapAxiosResponseBody(response: unknown): unknown {
  if (response === null || typeof response !== "object") return response;
  const r = response as Record<string, unknown>;

  if (
    "data" in r &&
    "status" in r &&
    typeof r.status === "number" &&
    "config" in r &&
    typeof r.config === "object"
  ) {
    return r.data;
  }

  return response;
}

/**
 * 从 axios 原始响应或已解包 body 中取出唯一合法的 `PageResult`。
 * 支持：① 严格 `PageResult`；② `ApiResponse.data` 嵌套；③ `records/current/size` 等常见变体。
 */
function extractPageResultPayload(response: unknown): PageResult<unknown> | null {
  const candidates: unknown[] = [];

  const push = (x: unknown) => {
    if (x !== undefined && x !== null) candidates.push(x);
  };

  push(response);
  push(unwrapAxiosResponseBody(response));

  for (let i = 0; i < candidates.length; i++) {
    const x = candidates[i];
    if (isPageResultPayload(x)) {
      return x;
    }
    const normalized = normalizePageResultLike(x);
    if (normalized) {
      return normalized;
    }
    if (x && typeof x === "object" && !Array.isArray(x) && "data" in x) {
      const inner = (x as Record<string, unknown>).data;
      push(inner);
      if (inner && typeof inner === "object" && !Array.isArray(inner) && "data" in inner) {
        push((inner as Record<string, unknown>).data);
      }
    }
  }

  return null;
}

export interface BaseRequestParams extends PageQuery {
  [key: string]: unknown;
}

export interface TableError {
  code: string;
  message: string;
  details?: unknown;
}

export const defaultResponseAdapter = <T>(response: unknown): ApiResponse<T> => {
  const pr = extractPageResultPayload(response);
  if (!pr) {
    console.error(
      "[tableUtils] 分页列表响应必须符合全局 PageResult<T>（items、total、page_no、page_size、has_next）；或为 ApiResponse 且 data 为该结构。收到:",
      response
    );
    return { records: [], total: 0, current: 1, size: 10 };
  }

  return {
    records: pr.items as T[],
    total: pr.total,
    current: pr.page_no,
    size: pr.page_size,
    has_next: pr.has_next,
  };
};

export const extractTableData = <T>(response: ApiResponse<T>): T[] => {
  const rows = response.records;
  return Array.isArray(rows) ? rows : [];
};

export const updatePaginationFromResponse = <T>(
  pagination: { total: number },
  response: ApiResponse<T>
): void => {
  const total = response.total;
  if (typeof total === "number") (pagination as Record<string, unknown>).total = total;
};

export const createSmartDebounce = <T extends (...args: any[]) => Promise<any>>(
  fn: T,
  delay: number
): T & { cancel: () => void; flush: () => Promise<any> } => {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  let lastArgs: Parameters<T> | null = null;
  let waiters: Array<{ resolve: (value: any) => void; reject: (reason: any) => void }> = [];

  const debouncedFn = (...args: Parameters<T>): Promise<any> => {
    return new Promise((resolve, reject) => {
      if (timeoutId) clearTimeout(timeoutId);
      lastArgs = args;
      waiters.push({ resolve, reject });
      timeoutId = setTimeout(async () => {
        const currentWaiters = waiters;
        const currentArgs = lastArgs;
        waiters = [];
        timeoutId = null;
        lastArgs = null;
        try {
          const result = await fn(...(currentArgs as Parameters<T>));
          currentWaiters.forEach((w) => w.resolve(result));
        } catch (error) {
          currentWaiters.forEach((w) => w.reject(error));
        }
      }, delay);
    });
  };

  debouncedFn.cancel = () => {
    if (timeoutId) clearTimeout(timeoutId);
    timeoutId = null;
    lastArgs = null;
    const currentWaiters = waiters;
    waiters = [];
    currentWaiters.forEach((w) => w.reject(new Error("Debounce cancelled")));
  };

  debouncedFn.flush = async () => {
    if (timeoutId && lastArgs && waiters.length) {
      clearTimeout(timeoutId);
      timeoutId = null;
      const currentWaiters = waiters;
      const currentArgs = lastArgs;
      waiters = [];
      lastArgs = null;

      try {
        const result = await fn(...(currentArgs as Parameters<T>));
        currentWaiters.forEach((w) => w.resolve(result));
        return result;
      } catch (error) {
        currentWaiters.forEach((w) => w.reject(error));
        throw error;
      }
    }
  };

  return debouncedFn as T & { cancel: () => void; flush: () => Promise<any> };
};

export const createErrorHandler = (
  onError?: (error: TableError) => void,
  enableLog: boolean = false
) => {
  return (error: any, defaultMessage: string = "操作失败"): TableError => {
    const tableError: TableError = {
      code: error?.code || "UNKNOWN_ERROR",
      message: error?.message || defaultMessage,
      details: error,
    };

    if (enableLog) {
      console.error("[tableUtils]", tableError);
    }

    onError?.(tableError);
    return tableError;
  };
};
