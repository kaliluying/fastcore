const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { runAcceptance } = require("./ux-e2e.cjs");

async function run() {
  const frontend = process.env.UX_E2E_FRONTEND || "http://127.0.0.1:5179";
  const backend = process.env.UX_E2E_BACKEND || "http://127.0.0.1:8009";
  const evidence = await (await fetch(`${backend}/__ux_e2e/evidence`)).json();
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ reducedMotion: "reduce" });
  const errors = [];
  const captures = [];
  page.on("pageerror", (error) => errors.push(error.message));

  try {
    await page.goto(`${frontend}/#/login`);
    await page.locator(".auth-top-bar__site-title").waitFor();
    assert.equal(await page.locator(".auth-top-bar__site-title").textContent(), "FastCore");
    assert.match(await page.title(), /FastCore/);
    assert.equal(
      await page.locator(".fa-enterprise-intro__footer span").first().textContent(),
      "FastCore"
    );
    for (const align of ["right", "left", "center"]) {
      await page.evaluate((value) => localStorage.setItem("login-panel-align", value), align);
      await page.reload();
      await page.locator('input[name="username"]').waitFor();
      for (const theme of ["light", "dark"]) {
        await page.evaluate(async (value) => {
          const { useSettingsStore } = await import("/src/store/modules/setting.store.ts");

          useSettingsStore().setGlopTheme(value, value);
        }, theme);
        for (const [width, height] of [
          [375, 844],
          [768, 900],
          [1024, 900],
          [1440, 960],
          [2048, 1120],
          [1440, 620],
          [667, 375],
        ]) {
          await page.setViewportSize({ width, height });
          await page.waitForFunction((expectedHeight) => {
            const root = document.querySelector(".login-page-root");
            return root && Math.abs(root.clientHeight - expectedHeight) <= 2;
          }, height);
          await page.locator(".login-page-panel__main").evaluate((panel) => (panel.scrollTop = 0));
          await page.waitForTimeout(100);
          assert.equal(
            await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
            false,
            `${align}/${theme}/${width}: horizontal overflow`
          );
          if (align !== "center" && width > 1180) {
            const content = await page.locator(".fa-enterprise-intro__content").boundingBox();
            const panel = await page.locator(".fa-enterprise-intro").boundingBox();
            assert.ok(content.y + content.height <= panel.y + panel.height);
          }
          if (height >= 620) {
            const button = await page.locator(".login-submit-btn").boundingBox();
            const panel = await page.locator(".login-page-panel__main").boundingBox();
            assert.ok(button.y >= panel.y && button.y + button.height <= panel.y + panel.height);
          }
          const filename = `login-${align}-${theme}-${width}-${height}.png`;
          await page.screenshot({ path: path.join(evidence.artifacts, filename) });
          await page.locator(".login-submit-btn").scrollIntoViewIfNeeded();
          const button = await page.locator(".login-submit-btn").boundingBox();
          assert.ok(button.y >= 0 && button.y + button.height <= height);
          captures.push({ align, theme, width, height, filename });
        }
      }
    }

    await page.setViewportSize({ width: 375, height: 844 });
    const settings = page.locator(".auth-mobile-settings");
    await settings.click();
    await page.locator("#auth-page-settings").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    assert.equal(await settings.getAttribute("aria-expanded"), "false");
    assert.ok(await settings.evaluate((button) => button === document.activeElement));
    await page.locator("#auth-page-settings").waitFor({ state: "hidden" });

    await page.evaluate(() => localStorage.setItem("login-panel-align", "right"));
    await page.setViewportSize({ width: 1440, height: 960 });

    let captchaRequests = 0;
    await page.route("**/system/auth/captcha/get", async (route) => {
      captchaRequests += 1;
      if (captchaRequests === 1) {
        await route.fulfill({ status: 503, json: { code: 503, msg: "验收用验证码故障" } });
      } else {
        await route.continue();
      }
    });
    await page.reload();
    await page
      .locator('.auth-feedback[role="alert"]')
      .filter({ hasText: "验证码加载失败" })
      .waitFor();
    await page.getByRole("button", { name: "重新获取验证码" }).click();
    await page.locator('.auth-feedback[role="alert"]').waitFor({ state: "hidden" });
    assert.equal(captchaRequests, 2);
    await page.unroute("**/system/auth/captcha/get");

    await page.route("**/system/auth/captcha/get", async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      payload.data.enable = true;
      await route.fulfill({ response, json: payload });
    });
    await page.setViewportSize({ width: 375, height: 844 });
    await page.reload();
    await page.locator(".captcha-row input").waitFor();
    const challenge = page.waitForResponse("**/system/auth/captcha/get");
    await page.getByRole("button", { name: "刷新验证码" }).click();
    await challenge;
    await page.locator(".captcha-image img").waitFor();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false
    );
    for (const theme of ["light", "dark"]) {
      await page.evaluate(async (value) => {
        const { useSettingsStore } = await import("/src/store/modules/setting.store.ts");

        useSettingsStore().setGlopTheme(value, value);
      }, theme);
      await page.screenshot({
        path: path.join(evidence.artifacts, `login-mobile-captcha-${theme}.png`),
      });
    }
    await page.unroute("**/system/auth/captcha/get");
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.reload();
    await page.locator(".login-submit-btn").click();
    await page.locator(".el-form-item__error").first().waitFor();
    await page.locator('input[name="username"]').fill("admin");
    await page.locator('input[name="password"]').fill("invalid-password");
    await page.locator(".login-submit-btn").click();
    await page
      .locator('.auth-feedback[role="alert"]')
      .filter({ hasText: "账号或密码错误" })
      .waitFor();
    await page.locator('input[name="password"]').fill("admin123");
    await page.locator('input[name="password"]').press("Enter");
    await page.waitForURL("**/#/home");
    assert.equal(errors.length, 0, errors.join("\n"));
    fs.writeFileSync(
      path.join(evidence.artifacts, "login-results.json"),
      JSON.stringify(
        {
          captures,
          pageErrors: errors,
          mobileSettings: true,
          validation: true,
          errorRecovery: true,
          captchaRecovery: "503 injected; retry returned a real challenge",
          captchaLayout:
            "visibility forced for mobile render; challenge and refresh used real HTTP",
          realLogin: true,
          boundary: evidence.boundary,
        },
        null,
        2
      )
    );
    console.log(`Login acceptance passed: ${captures.length} renders; ${evidence.artifacts}`);
  } finally {
    await browser.close();
  }
}

runAcceptance(run).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
