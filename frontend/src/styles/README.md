# FastCore 管理端视觉与样式

[文档目录](../../../docs/README.md) · [管理端说明](../../README.md) · [测试与验收](../../../docs/TESTING.md)

当前界面用于企业后端骨架的单组织管理端，支持系统管理、知识库和 AI 工作。视觉事实来源是 `core/_fa-tokens.scss` 与对应页面组件：淡蓝灰画布、蓝色强调、中性表面，并在登录页、首页与 AI 对话中使用 Liquid Glass / Bento 深度 Token。操作密集的页面保持较高信息密度，状态色只表达状态。

完整的颜色、排版、布局和首页信息层级约定见 [前端设计系统](../../../design-system/fastcore-quiet-operations/MASTER.md)。根目录 `dashboard-beautify-preview.html` 只保留早期色板参考，其中的示例指标、活动和日志不是产品数据，也不代表当前页面构图。实现与文档不一致时，先核对当前代码和用户已确认的设计方向，再同步更新文档。

## 当前实现

- `core/_fa-tokens.scss`：语义颜色、间距、圆角、动效，以及玻璃态 / Bento 表面变量，含深色模式表面层级。
- `core/_fa-type.scss`：中文优先的字体回退、字号和数字排版。
- `core/app.scss`：通用页面容器、管理页和表格卡片。
- `element-plus/_overrides.scss`、`element-plus/_dark.scss`：Element Plus 的组件状态与深色模式。
- `tailwind.css`：Tailwind 入口及主题变量映射。
- `index.scss`：全局样式入口。
- 具体页面的布局和状态样式留在对应 Vue 组件或同目录 SCSS 中。

入口文件 `frontend/src/main.ts` 依次加载 `tailwind.css`、`index.scss` 和动画库。运行时主题色由设置模块写入 `--theme-color`；新增强调色应优先使用该变量或现有语义变量。

## 页面约定

- 登录页介绍区、首页工作台和 AI 对话页共用同一语义色与玻璃表面体系。
- 首页只展示实际授权的快捷入口，以及有查询权限时的近七日登录活动；基础服务正常时不额外展示状态卡，异常时给出说明和重试入口。
- 不用账号资料、角色或权限计数、模块数量和会话检查清单填充首页；新增指标须先有真实数据来源和使用场景。
- `FaPageHeader` 是管理页面标题与操作按钮的公共入口；系统管理页和 AI 管理页都使用它。
- 列表页以一个主要操作和紧凑筛选区组织内容。移动端宽表格应能横向滚动，优先显示名称等识别字段。
- 健康、加载、空态和错误态都必须提供文字说明；颜色只用于辅助区分。
- 保留键盘焦点、深色模式和减少动态效果设置，避免只在浅色桌面视图下调样式。

## 验证

新 checkout 先通过 Vite 生成自动导入声明，再从 `frontend/` 执行 `corepack pnpm type-check`、`corepack pnpm test` 和 `corepack pnpm build`。样式改动可对目标文件运行 `corepack pnpm exec stylelint <文件>`。页面视觉需在浏览器检查桌面与窄屏；涉及真实菜单、登录或后端数据的结果还需在连接目标后端的环境中验收。
