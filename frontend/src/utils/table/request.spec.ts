import { describe, expect, it, vi } from "vitest";
import { createSmartDebounce } from "./request";

describe("createSmartDebounce", () => {
  it("resolves all callers when debounced execution completes", async () => {
    vi.useFakeTimers();
    const action = vi.fn().mockResolvedValue("done");
    const debounced = createSmartDebounce(action, 200);

    const p1 = debounced("first");
    const p2 = debounced("second");

    expect(action).not.toHaveBeenCalled();

    vi.advanceTimersByTime(250);
    const [r1, r2] = await Promise.all([p1, p2]);

    expect(r1).toBe("done");
    expect(r2).toBe("done");
    expect(action).toHaveBeenCalledTimes(1);
    expect(action).toHaveBeenCalledWith("second");

    vi.useRealTimers();
  });

  it("rejects all waiting callers when cancel is invoked", async () => {
    vi.useFakeTimers();
    const action = vi.fn().mockResolvedValue("done");
    const debounced = createSmartDebounce(action, 200);

    const p1 = debounced("arg");
    debounced.cancel();

    await expect(p1).rejects.toThrow("Debounce cancelled");
    expect(action).not.toHaveBeenCalled();

    vi.useRealTimers();
  });
});
