# FastCore

FastCore 是面向**单组织后台**的全栈项目骨架：FastAPI 提供认证、RBAC、菜单、审计、文件和健康检查，Vue 3 管理端提供系统管理与授权导航，并默认内置 AI 对话、知识库、记忆、用量和检索质量能力。业务模块通过显式注册接入，适合复制后裁剪并继续开发。

项目源自 [FastapiAdmin](https://github.com/fastapiadmin/FastapiAdmin) 的二次开发；该名称只表示来源，不代表运行时兼容关系。

[English](README.en.md) · [文档目录](docs/README.md) · [架构与开发](docs/ARCHITECTURE_AND_DEVELOPMENT.md) · [协作规则](AGENTS.md) · [术语表](GLOSSARY.md)

## 先读这里

| 目标             | 先看                                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 启动项目         | 本文「本地启动」，再读 [后端说明](backend/README.md)、[管理端说明](frontend/README.md)                                               |
| 理解运行链路     | [架构与开发](docs/ARCHITECTURE_AND_DEVELOPMENT.md)                                                                                   |
| 新增业务模块     | [标准模块模板](docs/STANDARD_MODULE_TEMPLATE.md) → [模块生成器](docs/MODULE_GENERATOR.md) → [模块增删改](docs/MODULE_DEVELOPMENT.md) |
| 修改 AI / 知识库 | [架构与开发](docs/ARCHITECTURE_AND_DEVELOPMENT.md) → [文档管理](docs/DOCUMENT_MANAGEMENT.md) → [对话流](docs/AI_CHAT_STREAMING.md)   |
| 判断如何验收     | [测试与验收](docs/TESTING.md) → [真实运行态验收](backend/integration/README.md)                                                      |
| 备份、恢复或部署 | [后端生产生命周期](backend/README.md) → [备份与恢复](docs/BACKUP_AND_RESTORE.md)                                                     |
| 给 AI 下修改任务 | 本文「仓库地图」「给 AI 的上下文」和 [AGENTS.md](AGENTS.md)                                                                          |

**事实优先级**：当前源码、依赖声明、有效环境配置和本次运行结果 > 当前使用文档 > 带日期的历史记录。`docs/` 中的验收报告、设计讨论和升级记录保留当时语境，不作为最新命令或当前行为依据。

## 技术栈与依赖边界

| 层             | 技术                                                                          | 声明文件                                                                                                                                          |
| -------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 后端           | Python 3.12+、FastAPI、SQLAlchemy 2、Alembic、Typer、Uvicorn                  | [backend/pyproject.toml](backend/pyproject.toml)、[backend/uv.lock](backend/uv.lock)                                                              |
| 前端           | Node.js 22.22.2+ / 24.15+ / 26+、Vue 3、TypeScript、Vite、Pinia、Element Plus | [frontend/package.json](frontend/package.json)、[frontend/pnpm-lock.yaml](frontend/pnpm-lock.yaml)                                                |
| 运行依赖       | MySQL 或 PostgreSQL、Redis                                                    | [backend/env/.env.dev.example](backend/env/.env.dev.example)                                                                                      |
| AI（默认安装） | Chroma、BM25/Whoosh、`fastembed`、LangChain 适配层与模型 SDK                  | [backend/pyproject.toml](backend/pyproject.toml) 的 `ai` 组、[backend/app/plugin/module_ai/plugin.toml](backend/app/plugin/module_ai/plugin.toml) |

MySQL 与 PostgreSQL 是正式支持的运行/生产数据库；**SQLite 仅用于自动化测试和隔离验收**。Redis 是启动、会话、限流与任务协调的必需依赖。对话或远程 embedding 服务商凭据、扫描 PDF 所需的本地 OCR 工具需要单独准备；复制项目后可整套裁剪 AI，见[模块增删改指南](docs/MODULE_DEVELOPMENT.md)。

## 仓库地图

```text
FastCore/
├── backend/                       # FastAPI 应用、CLI、迁移、测试与集成验收
│   ├── main.py                    # run / bootstrap / migrate / revision / generate-module
│   ├── app/init_app.py            # 中间件、基础路由、生命周期、启动迁移
│   ├── app/config/modules.py      # 业务模块显式清单（唯一增删入口）
│   ├── app/core/modules.py        # ModuleSpec 与统一装配
│   ├── app/api/v1/module_common/  # 文件、健康检查
│   ├── app/api/v1/module_platform/# 菜单
│   ├── app/api/v1/module_system/  # 认证、用户、角色、日志
│   ├── app/api/v1/module_demo/    # 仅 dev 启用的分类范例
│   ├── app/plugin/module_ai/      # AI 子系统及 plugin.toml 清单
│   ├── app/alembic/versions/      # 已纳入版本管理的迁移
│   └── tests/、integration/       # 自动化测试与真实依赖隔离验收
├── frontend/                      # Vue 3 管理端
│   ├── src/api/、src/views/       # 接口封装与业务页面
│   ├── src/router/                # 静态路由、授权菜单转换、动态注册与守卫
│   └── src/store/、src/utils/http/# 登录态、菜单状态与 Axios 请求
├── docs/                          # 当前架构、开发、测试、AI 与运维专题
├── design-system/                 # 视觉设计与页面设计记录
├── AGENTS.md                      # 本仓库协作约束
├── CLAUDE.md                      # Claude Code 项目入口
└── GLOSSARY.md                    # 业务术语
```

### 启动后发生了什么

1. `backend/main.py` 先设置 `ENVIRONMENT`，再导入配置；配置读取 `backend/env/.env.dev` 或 `.env.prod`。因此后端命令从 `backend/` 执行，独立脚本也要保持这个顺序。
2. `create_app()` 在 `app/init_app.py:register_routers()` 显式挂载 `module_common`、`module_platform` 和 `module_system`；其余模块由 `app/config/modules.py` 中的 `get_module` 入口返回 `ModuleSpec`，再经 `app/core/modules.py` 接入路由、模型、权限、种子、生命周期、索引和备份。**没有目录扫描式插件发现，也没有运行时插件开关**。
3. 应用启动在 Redis 锁下执行已提交的 Alembic 迁移、创建缺失结构、补齐稳定种子并校验结构，然后运行模块启动钩子。启动会写入环境配置指向的数据库，先确认目标。
4. 前端采用 Hash 路由。登录用户信息中的授权菜单经 `MenuProcessor` 转换，由 `RouteRegistry` 校验并动态注册，`beforeEach.ts` 负责导航守卫；**后端 `AuthPermission` 才是最终接口权限**，页面可见不代表有权限。
5. 路由声明包含 `/common/*`、`/platform/*`、`/system/*`、`/demo/*`（仅 dev）和 `/ai/*`。前端开发请求通过 `/api/v1` 代理；应用 `ROOT_PATH`（当前为 `/api/v1`）用于部署前缀和 OpenAPI 地址，直接访问路径是否需要该前缀取决于反向代理传入的实际路径。Swagger UI 位于 `DOCS_URL` 配置路径，以有效环境配置为准。

当前已注册模块是开发环境分类范例、首页运营概览和 AI。分类范例的 `development_only=True` 只影响路由、种子、钩子与用户菜单，不删除模型或历史迁移；正式业务应使用独立功能组、路由名和权限前缀。

## 本地启动

### 1. 准备配置

需要 Python 3.12+、`uv`、受支持的 Node.js 版本、通过 Corepack 使用仓库声明的 pnpm（`pnpm@12.11.2`），以及可访问的 MySQL/PostgreSQL 和 Redis。先准备**独立的开发数据库和 Redis DB**，不要指向生产实例。

```bash
git clone git@github.com:kaliluying/fastcore.git
cd fastcore
cp backend/env/.env.dev.example backend/env/.env.dev
```

填写 `SECRET_KEY`、数据库、Redis、AI 模型与存储目录配置。生产环境 `SECRET_KEY` 至少 32 字符；密钥和真实凭据只保留在本地环境文件，不提交、不写入文档或日志。

然后核对：

- 后端 `SERVER_HOST` / `SERVER_PORT` 与 `ROOT_PATH`；本仓库已检入的前端代理目标为 `9000`。后端有效 `.env.dev` 是本地忽略文件，新克隆从模板复制后会把示例端口设为 `8001`，需按前端代理改为一致。
- 前端 `frontend/.env` 的 `VITE_APP_BASE_API` 是浏览器使用的 API 前缀，`frontend/.env.development` 的 `VITE_API_BASE_URL` 是 Vite 代理目标；两者必须与后端实际监听地址一致。本仓库已检入的配置为 `/api/v1` → `http://localhost:9000`，后端有效 `.env.dev` 是本地忽略文件，新克隆从模板复制后需把示例端口改成一致。
- `VITE_ACCESS_MODE` 不决定动态菜单来源；登录后的 `routeList` 才决定可注册页面。AI 对话 SSE 也使用同一 HTTP 代理。
- 修改环境文件后重启 Vite；`.env.development` 会覆盖 `.env` 同名值。

### 2. 启动后端

```bash
cd backend
uv sync
uv run main.py run --env=dev
```

`uv sync` 默认安装完整 AI 与开发工具。启动成功后检查 `DOCS_URL`（Swagger UI）及 `/common/health/ready`（经前端代理时通常为 `/api/v1/common/health/ready`）；实际 URL 受 `ROOT_PATH` 和反向代理配置影响。

### 3. 启动前端

另开终端：

```bash
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

首次 checkout 若缺少自动导入类型声明，按[前端说明](frontend/README.md)先运行一次 Vite 构建，再做类型检查。前端开发端口由 `VITE_PORT` 决定（当前为 `5180`）。

> **会改变持久化状态的命令**：`uv run main.py run` 以及 `bootstrap` / `migrate` 会连接配置指定的数据库并执行迁移或初始化；`reset` 会删除所有表并重建，不能当作启动修复手段。`frontend` 的 `pnpm lint` 会修复/重写文件，验证前先确认工作区差异。

### 4. 部署前显式命令

准备 `backend/env/.env.prod`、备份、存储和原生数据库备份客户端后，从 `backend/` 执行：

```bash
uv run main.py bootstrap --env=prod   # 首次部署显式初始化，不清空现有数据
uv run main.py migrate --env=prod     # 后续部署显式应用已审查迁移
uv run main.py run --env=prod         # 正式启动；仍会自动迁移、初始化并校验
```

生成迁移用 `uv run main.py revision --env=dev`，人工审查 `backend/app/alembic/versions/` 中的升级/降级和已有数据影响后再交付。详见[后端说明](backend/README.md)和[备份与恢复](docs/BACKUP_AND_RESTORE.md)。

## 常见开发路径

### 新增业务模块

从 `backend/` 先预览生成器差异，确认后再写入：

```bash
uv run --no-sync main.py generate-module \
  --module business --resource supplier --title 供应商 --module-title 业务管理
uv run --no-sync main.py generate-module \
  --module business --resource supplier --title 供应商 --module-title 业务管理 --apply
```

生成器会创建后端 CRUD、迁移、回归测试、前端 API/页面、菜单种子，并在 `app/config/modules.py` 追加一条注册项；不会覆盖已有文件，也不会连接数据库。生成后必须审查业务约束与迁移、运行生成的测试、核对前端类型检查，并重新以管理员登录，在角色管理中为普通角色显式授权。详见[生成器文档](docs/MODULE_GENERATOR.md)和[标准模板](docs/STANDARD_MODULE_TEMPLATE.md)。

### 修改跨层功能

| 改动              | 必须同步检查                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------- |
| 后端字段 / Schema | ORM、Pydantic Schema、迁移、前端类型、表单、列表、服务端校验和刷新后持久化                                          |
| 权限或菜单        | Controller 权限码、`ModuleSpec.permissions`、页面按钮、菜单种子、已有角色授权及直接 URL                             |
| 路由或组件路径    | Router、前端 `src/api`、菜单 `route_path` / `route_name` / `component_path`、`MenuProcessor`、`RouteRegistry`、守卫 |
| AI 能力           | `plugin.toml` 声明、`module.py` 组合根、`chat` / `knowledge` / `memory` / `agent` / `quality` 边界和公开入口        |
| 知识文档          | 关系库记录、私有原件、Chroma、BM25、索引状态、失败恢复、删除与实际召回                                              |

AI 模块内部依赖保持单向：`agent → chat / knowledge`、`quality → chat / knowledge`、`chat → memory / knowledge`，`memory` 与 `knowledge` 不反向依赖其他 AI 功能。跨模块读取使用拥有方公开用例，不直接触达对方 CRUD、索引或 Provider Client；约束由 `backend/tests/test_module_boundaries.py` 检查。

### 裁剪已有能力

仅在**新项目副本**中操作：停用服务并备份 → 从模块清单删除注册项 → 删除前后端源码、专用测试与依赖 → 核对菜单清理预览再 `--apply` → 更新锁文件与生产 requirements → 重新登录验证菜单、直接路径、API、基础后台和构建。保留历史迁移与已有业务数据；裁剪和删表是两件独立的事。完整步骤见[模块增删改指南](docs/MODULE_DEVELOPMENT.md)。

## 验证与验收

按改动风险选择检查；局部通过不等于生产可用。

```bash
# backend/：安装依赖后
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests --no-fix --output-format concise

# frontend/：按 lockfile 安装依赖后
corepack pnpm exec vite build   # 新 checkout 先生成自动导入类型声明
corepack pnpm type-check
corepack pnpm test
corepack pnpm build
```

- 后端 `tests/conftest.py` 使用临时 SQLite 和模拟 Redis，只证明测试夹具范围；不能证明 MySQL、PostgreSQL、真实 Redis、OCR 或外部模型可用。
- 前端 Vitest、类型检查和构建只证明编译与局部逻辑；跨层功能要有真实浏览器、API 响应、持久化、刷新与错误态证据。
- `frontend/tests/*.cjs` 和 `backend/integration/` 提供隔离浏览器、真实依赖、队列恢复、备份还原与迁移验收；前提、端口和证据边界见[测试与验收](docs/TESTING.md)及[真实运行态验收](backend/integration/README.md)。
- 知识库上传成功只代表文件与记录已接收，需要等待索引终态并检查实际召回；真实模型调用还需确认供应商、测试数据、成本与授权。

完整命令和各层证据边界以[测试与验收](docs/TESTING.md)为准；开发闭环与停止条件见[开发阶段 Loop](docs/DEVELOPMENT_LOOP.md)。

## 给 AI 的上下文

开始任务前：

1. 读本仓库 [AGENTS.md](AGENTS.md) 及适用目录的 `AGENTS.md`；按用户级规则处理授权、工作区保护、事实来源和交付证据。
2. 检查当前分支、`git status`、未跟踪文件和已有差异，保留用户修改；用最小范围改动完成任务。
3. 按「先读这里」定位当前源码与配置，不把文档示例、历史记录或旧提交状态当成当前事实；涉及符号调用链时优先使用 CodeGraph。
4. 修改前沿真实调用链检查 Controller / Service / ORM / 权限 / 菜单 / API / 页面 / 迁移 / 测试与所有调用方；先修共享入口的根因。
5. 运行最小充分验证，并区分通过、跳过、基线失败、模拟验收和未验证项；不能把局部测试描述成端到端验收。
6. 默认不提交、不推送、不发布，不使用 `reset` 或破坏性数据库命令；危险操作需要明确授权。

对 AI 特别容易误判的约束：

- 后端是**单组织**权限模型；前端菜单只管展示，最终权限由后端校验。
- 业务模块只通过 `app/config/modules.py` 显式注册；AI 同时受 `plugin.toml` 与 `module.py` 约束。
- API 与文档路径受 `ROOT_PATH` 和 `DOCS_URL` 影响；复制模板中的地址前先核对有效环境文件。
- 启动、迁移和初始化会写数据库；测试夹具的 SQLite 不代表正式数据库。
- 知识库是关系库 + 私有文件 + Chroma + BM25 的跨存储链路，没有分布式原子事务，必须检查恢复与终态。
- 分类管理范例只在 `ENVIRONMENT=dev` 活跃，正式业务不要沿用 `demo` 名称、权限前缀或 `development_only`。

## 文档维护

根 README 保留项目定位、最短启动路径、仓库地图和 AI 阅读入口；子项目 README 维护各自命令；[架构与开发指南](docs/ARCHITECTURE_AND_DEVELOPMENT.md)维护跨端调用链；[文档目录](docs/README.md)维护专题索引；历史报告保留原始日期与当时的验证结果。

名称与内部标识统一为 FastCore：默认数据库 `fastcore`、Redis 品牌前缀 `fastcore:` / `fastcore-cache:`、日志 `backend/logs/fastcore.log`、CLI 对象 `fastcore_cli`、设计目录 `design-system/fastcore-quiet-operations/`；本地目录名使用 `FastCore`。已有实例升级前应先备份、迁移数据库并同步环境配置，见[品牌迁移](docs/BRAND_MIGRATION.md)。

根目录 `dashboard-beautify-preview.html` 是早期静态视觉预览，仅作历史色板参考，不代表当前登录页、首页或 AI 对话实现。许可证见 [LICENSE](LICENSE)。
