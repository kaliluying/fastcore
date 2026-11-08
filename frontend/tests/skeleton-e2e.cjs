const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function run() {
  const [frontendPort, backendPort, artifacts, phase] = process.argv.slice(2);
  if (!frontendPort || !backendPort || !artifacts) {
    console.error(
      "Usage: node skeleton-e2e.cjs <frontendPort> <backendPort> <artifactsDir> [phase]"
    );
    process.exit(1);
  }
  const backend = `http://127.0.0.1:${backendPort}`;
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname) ||
      ["data:", "blob:"].includes(url.protocol)
      ? route.continue()
      : route.abort();
  });
  const checks = [];
  const name = "骨架验收供应商";
  const updatedName = `${name}-修改`;

  async function login(username) {
    const response = await fetch(`${backend}/system/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password: "admin123" }),
    });
    assert.equal(response.status, 200);
    return (await response.json()).data.access_token;
  }

  async function api(url, token, options = {}) {
    return fetch(`${backend}${url}`, {
      ...options,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
  }

  try {
    const admin = await login("admin");
    const info = await (await api("/system/user/current/info", admin)).json();
    assert.ok(info.data.modules.includes("business"));
    if (phase === "business") {
      const reader = await login("fixture_user_1");
      assert.equal(
        (
          await api("/business/supplier/create", reader, {
            method: "POST",
            body: JSON.stringify({ name: "无权限", contact_email: "reader@example.com" }),
          })
        ).status,
        403
      );
      for (const payload of [
        { name: "x".repeat(33) },
        { name: "邮箱校验", contact_email: "invalid-email" },
      ]) {
        assert.equal(
          (
            await api("/business/supplier/create", admin, {
              method: "POST",
              body: JSON.stringify(payload),
            })
          ).status,
          422
        );
      }
      checks.push("new module enforces permissions and changed field validation");
      const role = await api("/system/role/create", admin, {
        method: "POST",
        body: JSON.stringify({ name: "普通业务角色", code: "skeleton_business", data_scope: 4 }),
      });
      assert.equal(role.status, 200);
      const roleId = (await role.json()).data.id;
      const menuTree = (await (await api("/platform/menu/tree", admin)).json()).data;
      const flatten = (nodes) => nodes.flatMap((node) => [node, ...flatten(node.children || [])]);
      const menuIds = flatten(menuTree)
        .filter(
          (menu) =>
            menu.route_name === "Business" || menu.permission?.startsWith("module_business:")
        )
        .map((menu) => menu.id);
      assert.ok(menuIds.length >= 6);
      const grant = await api("/system/role/permission", admin, {
        method: "PUT",
        body: JSON.stringify({ role_ids: [roleId], menu_ids: menuIds, data_scope: 4 }),
      });
      assert.equal(grant.status, 200);
      const user = await api("/system/user/create", admin, {
        method: "POST",
        body: JSON.stringify({
          username: "business_user",
          name: "普通业务用户",
          password: "admin123",
          role_ids: [roleId],
          is_superuser: false,
        }),
      });
      assert.equal(user.status, 200);
      const ordinary = await login("business_user");
      const ordinaryInfo = (await (await api("/system/user/current/info", ordinary)).json()).data;
      assert.ok(!ordinaryInfo.permissions.includes("*:*:*"));
      assert.ok(ordinaryInfo.permissions.includes("module_business:supplier:create"));
      assert.ok(JSON.stringify(ordinaryInfo.menus).includes("module_business/supplier/index"));
      checks.push("real role grants expose the business menu and permissions to an ordinary user");
    }
    await page.goto(`http://127.0.0.1:${frontendPort}`);
    await page.locator('input[name="username"]').fill("business_user");
    await page.locator('input[name="password"]').fill("admin123");
    await page.locator('input[name="password"]').press("Enter");
    await page.getByText("业务管理", { exact: true }).first().waitFor({ timeout: 30000 });
    const skip = page.getByRole("button", { name: "跳过", exact: true });
    if (await skip.isVisible()) await skip.click();
    if (phase === "pruned") {
      assert.deepEqual(info.data.modules, ["business"]);
      assert.equal(await page.getByRole("heading", { name: "知识工作", exact: true }).count(), 0);
      assert.equal(await page.getByText("AI 知识库", { exact: true }).count(), 0);
      assert.equal(await page.getByText("近七日登录趋势", { exact: true }).count(), 0);
      for (const url of [
        "/ai/knowledge/list",
        "/demo/category/list",
        "/common/monitoring/login-trend",
      ]) {
        assert.equal((await api(url, admin)).status, 404);
      }
      checks.push("removed menus, homepage sections and APIs are absent");
    }
    const supplierMenu = page.locator(".el-menu-item").filter({ hasText: "供应商管理" });
    if (!(await supplierMenu.isVisible())) {
      await page.locator(".el-sub-menu__title").filter({ hasText: "业务管理" }).click();
    }
    await supplierMenu.click();
    await page.getByRole("heading", { name: "供应商管理", exact: true }).waitFor();
    if (phase === "business") {
      await page.getByRole("button", { name: "新增供应商", exact: true }).click();
      const dialog = page.locator(".el-dialog");
      await dialog.getByLabel("供应商名称", { exact: true }).fill(name);
      await dialog.getByLabel("联系邮箱", { exact: true }).fill("first@example.com");
      await dialog.getByRole("button", { name: "保存", exact: true }).click();
      const row = page.getByRole("row").filter({ hasText: name });
      await row.waitFor();
      await row.getByRole("button", { name: "编辑", exact: true }).click();
      assert.equal(
        await dialog.getByLabel("联系邮箱", { exact: true }).inputValue(),
        "first@example.com"
      );
      await dialog.getByLabel("供应商名称", { exact: true }).fill(updatedName);
      await dialog.getByLabel("联系邮箱", { exact: true }).fill("updated@example.com");
      await dialog.getByRole("button", { name: "保存", exact: true }).click();
      checks.push("real UI creates and updates the added email field");
    }
    const persistedRow = page.getByRole("row").filter({ hasText: updatedName });
    await persistedRow.waitFor();
    assert.match(await persistedRow.innerText(), /updated@example\.com/);
    await page.reload();
    await persistedRow.waitFor();
    const routeBeforeSkip = page.url();
    await page.keyboard.press("Tab");
    assert.ok(
      await page
        .getByRole("link", { name: "跳到主要内容", exact: true })
        .evaluate((link) => link === document.activeElement)
    );
    await page.keyboard.press("Enter");
    assert.equal(page.url(), routeBeforeSkip);
    assert.equal(await page.evaluate(() => document.activeElement.id), "app-content");
    checks.push("keyboard skip link focuses content without changing the hash route");
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      if (width === 375) {
        await page.locator('.header-collapse-btn[aria-expanded="false"]').waitFor();
        await page.locator(".menu-model").waitFor({ state: "hidden" });
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({
        path: path.join(artifacts, `${phase}-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      await page.getByRole("button", { name: "新增供应商", exact: true }).click();
      const dialog = page.locator(".el-dialog");
      await dialog.waitFor();
      await page.screenshot({
        path: path.join(artifacts, `${phase}-dialog-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      assert.ok(
        await dialog
          .locator(".el-form-item__label")
          .filter({ hasText: "供应商名称" })
          .evaluate((label) => {
            const rect = label.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + 2, rect.top + rect.height / 2);
            return rect.left >= 0 && rect.right <= innerWidth && label.contains(hit);
          }),
        "dialog label must be visible and uncovered by the sidebar"
      );
      await dialog.getByRole("button", { name: "取消", exact: true }).click();
    }
    checks.push("navigation, refresh, persisted business data and narrow layout work");
    checks.push("wide and narrow dialogs remain readable above the sidebar");
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(artifacts, `${phase}.json`),
      JSON.stringify({ passed: true, checks, errors }, null, 2)
    );
    console.log(JSON.stringify({ passed: true, phase, checks }));
  } catch (error) {
    await page
      .screenshot({ path: path.join(artifacts, `${phase}-failure.png`), fullPage: true })
      .catch(() => {});
    fs.writeFileSync(
      path.join(artifacts, `${phase}-failure.json`),
      JSON.stringify(
        {
          passed: false,
          error: error.message,
          errors,
          text: (await page.locator("body").innerText()).slice(0, 3000),
        },
        null,
        2
      )
    );
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
