const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const { test } = require("node:test");

const source = fs.readFileSync(
  path.join(__dirname, "../src/views/module_platform/menu/composables/useMenuManagement.ts"),
  "utf8"
);
const ast = ts.createSourceFile("useMenuManagement.ts", source, ts.ScriptTarget.Latest, true);
let submit;
function findSubmit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "handleSubmit") {
    submit = node;
    return;
  }
  ts.forEachChild(node, findSubmit);
}
findSubmit(ast);
const code = ts.transpileModule(submit.getText(ast), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function createFixture(validate, save = async () => {}) {
  const calls = { validate: 0, create: 0, update: 0, reset: 0, refresh: 0 };
  const state = {
    allowedMenuTypeValues: { value: [2] },
    formData: { value: { type: 2, name: "待保存菜单" } },
    dataFormRef: {
      value: {
        validate: () => {
          calls.validate++;
          return validate();
        },
      },
    },
    submitLoading: { value: false },
    MenuAPI: {
      createMenu: async () => {
        calls.create++;
        await save();
      },
      updateMenu: async () => {
        calls.update++;
        await save();
      },
    },
    dialogVisible: { visible: true },
    resetForm: async () => {
      calls.reset++;
    },
    loadMenuData: async () => {
      calls.refresh++;
    },
    refreshAuthorizationUi: async () => {},
    console: { error() {} },
  };
  vm.runInNewContext(code, state);
  return { state, calls };
}

test("menu submission locks before asynchronous validation", async () => {
  const validation = Promise.withResolvers();
  const { state, calls } = createFixture(() => validation.promise);
  const first = state.handleSubmit();
  await state.handleSubmit();
  assert.equal(state.submitLoading.value, true);
  assert.equal(calls.validate, 1);
  validation.resolve(true);
  await first;
  assert.equal(calls.create, 1);
  assert.equal(calls.reset, 1);
  assert.equal(state.submitLoading.value, false);
});

test("menu submission remains locked until the update completes", async () => {
  const request = Promise.withResolvers();
  const { state, calls } = createFixture(
    async () => true,
    () => request.promise
  );
  state.formData.value.id = 7;
  const first = state.handleSubmit();
  await Promise.resolve();
  await state.handleSubmit();
  assert.equal(calls.update, 1);
  assert.equal(calls.create, 0);
  request.resolve();
  await first;
  assert.equal(state.submitLoading.value, false);
});

test("invalid or unavailable forms release the lock without sending data", async () => {
  for (const validate of [
    async () => false,
    async () => {
      throw new Error("校验失败");
    },
    () => undefined,
  ]) {
    const { state, calls } = createFixture(validate);
    await state.handleSubmit();
    assert.equal(calls.create, 0);
    assert.equal(state.dialogVisible.visible, true);
    assert.equal(state.submitLoading.value, false);
  }
});

test("failed saves retain the draft and allow retry", async () => {
  let fail = true;
  const { state, calls } = createFixture(
    async () => true,
    async () => {
      if (fail) throw new Error("网络失败");
    }
  );
  await state.handleSubmit();
  assert.equal(state.formData.value.name, "待保存菜单");
  assert.equal(state.dialogVisible.visible, true);
  assert.equal(calls.reset, 0);
  assert.equal(state.submitLoading.value, false);
  fail = false;
  await state.handleSubmit();
  assert.equal(calls.create, 2);
  assert.equal(calls.refresh, 1);
});
