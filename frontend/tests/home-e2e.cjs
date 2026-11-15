const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { runAcceptance } = require("./ux-e2e.cjs");

async function run() {
  const frontend = "http://127.0.0.1:5179";
  const evidence = await (await fetch("http://127.0.0.1:8009/__ux_e2e/evidence")).json();
  const artifacts = evidence.artifacts;
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const checks = [];
  const checkLinkContrast = async () => {
    const contrasts = await page.locator(".knowledge-launch__link").evaluateAll((links) => {
      const context = document.createElement("canvas").getContext("2d");
      const luminance = (color) => {
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const channels = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map((channel) => {
          const value = channel / 255;
          return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        });
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      };
      return links.map((link) => {
        const foreground = luminance(getComputedStyle(link).color);
        const background = luminance(getComputedStyle(link.closest("a")).backgroundColor);
        return (
          (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
        );
      });
    });
    assert.ok(
      contrasts.every((ratio) => ratio >= 4.5),
      `low link contrast: ${contrasts}`
    );
  };
  let phase = "home";
  page.on("pageerror", (error) => errors.push({ phase, message: error.message }));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname) ||
      ["data:", "blob:"].includes(url.protocol)
      ? route.continue()
      : route.abort();
  });
  try {
    await page.goto(frontend);
    await page.locator('input[name="username"]').fill("admin");
    await page.locator('input[name="password"]').fill("admin123");
    const login = page.waitForResponse((response) => response.url().endsWith("/system/auth/login"));
    await page.locator('input[name="password"]').press("Enter");
    assert.equal((await login).status(), 200);
    await page.locator(".knowledge-launch__action").waitFor({ timeout: 30000 });
    const skip = page.getByRole("button", { name: "跳过", exact: true });
    await skip.waitFor({ timeout: 3000 }).catch(() => {});
    if (await skip.isVisible()) await skip.click();
    await page.locator(".home-health--healthy").waitFor();
    await page.locator(".trend-summary").waitFor();
    await page.locator(".el-notification").waitFor({ state: "hidden", timeout: 10000 });
    checks.push("real login, authorized menus, readiness and login trend");
    await checkLinkContrast();

    for (const width of [2560, 2048, 1440, 1024, 768, 375]) {
      phase = "responsive-shell";
      await page.setViewportSize({ width, height: width >= 1024 ? 1100 : 1000 });
      await page.mouse.move(0, 0);
      await page.waitForTimeout(700);
      await page.locator(".home-workspace").evaluate((node) => {
        for (let parent = node.parentElement; parent; parent = parent.parentElement)
          parent.scrollTop = 0;
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth
      );
      assert.equal(overflow, false, `horizontal overflow at ${width}`);
      if (width >= 1440) {
        const proportions = await page.locator(".home-workspace").evaluate((node) => {
          const content = document.querySelector("#app-content");
          return {
            workspace: node.getBoundingClientRect().width,
            content: content.getBoundingClientRect().width,
            cards: [...node.querySelectorAll(".knowledge-launch__resources a")].map((card) => ({
              top: card.getBoundingClientRect().top,
              height: card.getBoundingClientRect().height,
            })),
          };
        });
        assert.ok(proportions.workspace / proportions.content > 0.76, "workspace is too narrow");
        assert.equal(new Set(proportions.cards.map((card) => card.top)).size, 1);
        assert.ok(
          proportions.cards.every((card) => card.height < 300),
          "oversized launch panel"
        );
      }
      const links = await page
        .locator(".home-workspace a, .home-workspace button, .home-workspace summary")
        .evaluateAll((nodes) =>
          nodes.map((node) => {
            const rect = node.getBoundingClientRect();
            return {
              text: node.innerText,
              height: rect.height,
              width: rect.width,
              left: rect.left,
              right: rect.right,
            };
          })
        );
      for (const link of links) {
        assert.ok(link.height >= 44, `small tap target: ${link.text}`);
        assert.ok(link.left >= 0 && link.right <= width, `clipped link: ${link.text}`);
      }
      await page.screenshot({ path: path.join(artifacts, `home-${width}.png`), fullPage: true });
    }
    checks.push("2560/2048/1440/1024/768/375 layouts, workspace proportions and 44px targets");
    phase = "home";
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.keyboard.press("Tab");
    await page.locator(".trend-details summary").focus();
    assert.equal(
      await page
        .locator(".trend-details summary")
        .evaluate((node) => getComputedStyle(node).outlineStyle),
      "solid"
    );
    await page.keyboard.press("Enter");
    assert.equal(await page.locator(".trend-details").getAttribute("open"), "");
    assert.equal(await page.locator(".trend-details tbody tr").count(), 7);
    await page.keyboard.press("Enter");
    checks.push("keyboard focus and seven-day accessible data table");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".trend-details table").waitFor();
    assert.equal(await page.locator(".trend-chart").count(), 0);
    checks.push("reduced motion uses static data instead of animated chart");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.locator(".trend-chart canvas").waitFor();
    await page.waitForTimeout(1800);
    await page.evaluate(async () => {
      const { useSettingsStore } = await import("/src/store/modules/setting.store.ts");

      useSettingsStore().setGlopTheme("dark", "dark");
    });
    await page.waitForTimeout(1800);
    await checkLinkContrast();
    const chartBounds = await page.locator(".trend-chart").evaluate((node) => {
      const canvas = node.querySelector("canvas");
      return {
        container: node.getBoundingClientRect().right,
        canvas: canvas?.getBoundingClientRect().right,
      };
    });
    assert.ok(
      chartBounds.canvas <= chartBounds.container + 1,
      "dark chart canvas exceeds its container"
    );
    await page.locator(".home-workspace").evaluate((node) => {
      for (let parent = node.parentElement; parent; parent = parent.parentElement)
        parent.scrollTop = 0;
    });
    await page.screenshot({ path: path.join(artifacts, "home-dark.png"), fullPage: true });
    await page.evaluate(async () => {
      const { useSettingsStore } = await import("/src/store/modules/setting.store.ts");

      useSettingsStore().setGlopTheme("light", "light");
    });
    checks.push("dark-mode rendering and AA link contrast in both default themes");

    const healthPattern = "**/common/health/ready";
    const healthy = await (
      await page.request.get("http://127.0.0.1:8009/api/v1/common/health/ready")
    ).json();
    await page.route(healthPattern, (route) =>
      route.fulfill({
        status: 503,
        json: {
          ...healthy,
          data: {
            ...healthy.data,
            status: 0,
            dependencies: {
              ...healthy.data.dependencies,
              database: { status: 0, enabled: true, latency_ms: null },
            },
          },
        },
      })
    );
    await page.getByRole("button", { name: /^刷新基础服务状态/ }).click();
    await page.locator(".home-health--degraded").waitFor();
    assert.match(await page.locator(".home-health").innerText(), /数据库连接异常/);
    await page.route(healthPattern, (route) => route.abort("failed"));
    await page.getByRole("button", { name: /^刷新基础服务状态/ }).click();
    await page.locator(".home-health--unavailable").waitFor();
    await page.unroute(healthPattern);
    await page.getByRole("button", { name: /^刷新基础服务状态/ }).click();
    await page.locator(".home-health--healthy").waitFor();
    checks.push("fixture degraded/unavailable states and recovery to real readiness");

    const trendPattern = "**/common/monitoring/login-trend";
    await page.route(trendPattern, (route) =>
      route.fulfill({ status: 500, json: { code: 500, msg: "fixture failure" } })
    );
    await page.getByRole("button", { name: "刷新登录趋势" }).click();
    await page.locator(".trend-state--error").waitFor();
    await page.route(trendPattern, (route) =>
      route.fulfill({ json: { code: 200, data: { items: [] } } })
    );
    await page.getByRole("button", { name: "刷新登录趋势" }).click();
    await page.getByText("近 7 日暂无成功登录记录").waitFor();
    await page.screenshot({ path: path.join(artifacts, "home-empty.png"), fullPage: true });
    await page.unroute(trendPattern);
    await page.getByRole("button", { name: "刷新登录趋势" }).click();
    await page.locator(".trend-chart").waitFor();
    checks.push("fixture trend error/empty states and recovery to real data");

    const destinations = [
      [".knowledge-launch__action", "/ai/chat", ".message-input textarea"],
      ['.knowledge-launch__resources a[href="#/ai/knowledge"]', "/ai/knowledge", ".knowledge-page"],
      ['.knowledge-launch__resources a[href="#/ai/document"]', "/ai/document", ".document-page"],
    ];
    for (const [selector, destination, target] of destinations) {
      phase = "destination-navigation";
      await page.locator(selector).click();
      await page.waitForURL(`**/#${destination}`);
      await page.locator(target).waitFor({ timeout: 20000 });
      await page.goto(`${frontend}/#/home`);
      await page.locator(".knowledge-launch__action").waitFor();
    }
    await page.waitForTimeout(500);
    phase = "home";
    checks.push("real home-to-chat/knowledge/documents navigation");
    const restricted = await browser.newPage({ viewport: { width: 375, height: 844 } });
    await restricted.route("**/*", (route) => {
      const url = new URL(route.request().url());
      return ["127.0.0.1", "localhost"].includes(url.hostname) ||
        ["data:", "blob:"].includes(url.protocol)
        ? route.continue()
        : route.abort();
    });
    await restricted.route("**/system/user/current/info", async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      await route.fulfill({
        response,
        json: {
          ...payload,
          data: { ...payload.data, menus: [], permissions: [], is_superuser: false },
        },
      });
    });
    let restrictedTrendRequests = 0;
    restricted.on("request", (request) => {
      if (request.url().endsWith("/common/monitoring/login-trend")) restrictedTrendRequests += 1;
    });
    await restricted.goto(frontend);
    await restricted.locator('input[name="username"]').fill("admin");
    await restricted.locator('input[name="password"]').fill("admin123");
    await restricted.locator('input[name="password"]').press("Enter");
    await restricted.locator(".home-empty").waitFor({ timeout: 30000 });
    const restrictedSkip = restricted.getByRole("button", { name: "跳过", exact: true });
    await restrictedSkip.waitFor({ timeout: 3000 }).catch(() => {});
    if (await restrictedSkip.isVisible()) await restrictedSkip.click();
    await restricted.locator(".el-notification").waitFor({ state: "hidden", timeout: 10000 });
    assert.equal(
      await restricted.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      false
    );
    assert.equal(
      await restricted.locator(".knowledge-launch, .pending-panel, .login-trend-card").count(),
      0
    );
    assert.equal(restrictedTrendRequests, 0);
    await restricted.screenshot({
      path: path.join(artifacts, "home-no-permissions.png"),
      fullPage: true,
    });
    await restricted.close();
    checks.push("fixture empty permissions hide entries and prevent trend requests");
    assert.deepEqual(errors, [], "unexpected browser runtime errors");
    fs.writeFileSync(
      path.join(artifacts, "home-results.json"),
      JSON.stringify({ checks, errors }, null, 2)
    );
    console.log(JSON.stringify({ checks, artifacts, errors }));
  } catch (error) {
    await page
      .screenshot({ path: path.join(artifacts, "home-failure.png"), fullPage: true })
      .catch(() => {});
    fs.writeFileSync(
      path.join(artifacts, "home-results.json"),
      JSON.stringify({ checks, errors, failure: error.message }, null, 2)
    );
    throw error;
  } finally {
    await browser.close();
  }
}

runAcceptance(run).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
