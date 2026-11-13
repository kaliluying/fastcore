import { ElMessage } from "element-plus";
import { describe, expect, it, vi } from "vitest";
import { HttpError, request } from "./index";

vi.mock("element-plus", () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

describe("http response interceptor business error handling", () => {
  it("rejects non-SUCCESS response as HttpError and respects showErrorMessage: false", async () => {
    const errorSpy = vi.mocked(ElMessage.error);
    errorSpy.mockClear();

    // 模拟 Axios 适配器直接返回业务失败响应
    const customAdapter = async (config: any) => ({
      data: { code: 4001, msg: "业务校验未通过", data: { field: "username" } },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    try {
      await request.get("/test-business-err", {
        adapter: customAdapter,
        showErrorMessage: false,
      });
      expect.unreachable("should reject on code !== 200");
    } catch (error: unknown) {
      expect(error).toBeInstanceOf(HttpError);
      const httpErr = error as HttpError;
      expect(httpErr.message).toBe("业务校验未通过");
      expect(httpErr.code).toBe(4001);
      expect(httpErr.data).toEqual({
        code: 4001,
        msg: "业务校验未通过",
        data: { field: "username" },
      });
      expect(errorSpy).not.toHaveBeenCalled();
    }
  });

  it("calls ElMessage.error when showErrorMessage is not disabled", async () => {
    const errorSpy = vi.mocked(ElMessage.error);
    errorSpy.mockClear();

    const customAdapter = async (config: any) => ({
      data: { code: 5001, msg: "服务器忙", data: null },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    try {
      await request.get("/test-server-err", {
        adapter: customAdapter,
      });
      expect.unreachable("should reject on code !== 200");
    } catch (error: unknown) {
      expect(error).toBeInstanceOf(HttpError);
      expect(errorSpy).toHaveBeenCalledWith("服务器忙");
    }
  });
});
