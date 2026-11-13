import { ElNotification } from "element-plus";

const CHECK_INTERVAL = 60_000;

let checkTimer: number | undefined;
let updateNotified = false;

function versionManifestUrl(): string {
  const baseUrl = import.meta.env.BASE_URL.endsWith("/")
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`;
  return `${baseUrl}version.json?ts=${Date.now()}`;
}

export function stopVersionCheck(): void {
  if (checkTimer !== undefined) {
    window.clearInterval(checkTimer);
    checkTimer = undefined;
  }
}

function showUpdateNotification(): void {
  if (updateNotified) return;
  updateNotified = true;
  stopVersionCheck();

  ElNotification({
    title: "发现新版本",
    message: "发现新版本，点击刷新",
    type: "info",
    duration: 0,
    onClick: () => window.location.reload(),
  });
}

export async function checkForUpdate(): Promise<void> {
  try {
    const response = await fetch(versionManifestUrl(), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;

    const manifest = (await response.json()) as { buildTimestamp?: number };
    if (
      typeof manifest.buildTimestamp === "number" &&
      manifest.buildTimestamp !== __APP_INFO__.buildTimestamp
    ) {
      showUpdateNotification();
    }
  } catch {
    // 版本检查失败不影响正常使用，下一轮继续检查。
  }
}

export function startVersionCheck(): void {
  if (!import.meta.env.PROD || checkTimer !== undefined) return;

  void checkForUpdate();
  checkTimer = window.setInterval(() => void checkForUpdate(), CHECK_INTERVAL);
}
