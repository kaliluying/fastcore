# FastCore 后端

后端是单组织企业应用骨架，使用 FastAPI、SQLAlchemy 和 Alembic，提供认证、RBAC、菜单、审计、文件与健康检查，以及内置 AI 对话和知识库。跨端关系见[架构指南](../docs/ARCHITECTURE_AND_DEVELOPMENT.md)，依赖以 `pyproject.toml` 为准。

## 入口与模块

| 路径（相对 backend/）                                     | 职责                                         |
| --------------------------------------------------------- | -------------------------------------------- |
| `main.py`、`app/init_app.py`                              | CLI、应用组装、生命周期、路由与中间件        |
| `app/api/v1/module_common/`                               | 文件、健康检查与监控                         |
| `app/api/v1/module_platform/`                             | 菜单                                         |
| `app/api/v1/module_system/`                               | 认证、用户、角色和日志                       |
| `app/api/v1/module_demo/category/`                        | 开发环境分类范例                             |
| `app/plugin/module_ai/`                                   | 对话、知识库、记忆、模型配置、用量与质量评测 |
| `app/config/modules.py`、`app/core/modules.py` | 业务模块的路由、模型、种子、权限与生命周期组装 |
| `app/scripts/initialize.py`、`app/scripts/migrate.py`     | 初始化、迁移和生产结构检查                   |
| `app/alembic/versions/`                                   | 已纳入版本管理的迁移脚本                     |

`AuthPermission` 校验接口权限；前端菜单只控制导航与可见性。旧字典、参数模块已移除。AI 默认提供，依赖随 `uv sync` 安装；业务模块通过 `app/config/modules.py` 显式注册，增删改见[模块开发指南](../docs/MODULE_DEVELOPMENT.md)。

## 开发运行

所有后端命令从 `backend/` 执行。需要 Python 3.12+、`uv`、MySQL 或 PostgreSQL 与 Redis。MySQL 和 PostgreSQL 均为正式支持的运行数据库；SQLite 仅用于自动化测试和隔离验收，不用于生产部署。

```bash
cp env/.env.dev.example env/.env.dev
# 填写连接地址及密钥后执行
uv sync
uv run main.py run --env=dev
```

`main.py` 在导入配置前设置 `ENVIRONMENT`；独立脚本也要保持这个顺序。开发启动在 Redis 初始化锁下应用已有迁移、创建缺失表并补齐种子数据。启动会写入配置指定的数据库，先确认它是目标开发库。

默认 embedding 使用本地 `fastembed`；对话或远程 embedding 需要可用的模型服务。本地模型首次使用可能下载模型文件。真实密钥只放在本地环境配置中。

## 生产生命周期与迁移

准备 `env/.env.prod`，确认数据库、Redis、存储目录和备份。生产 `SECRET_KEY` 至少 32 字符，也用于保护已保存的模型凭据；更换前评估既有凭据的兼容性。

```bash
# 首次部署：初始化缺失结构和基础数据
uv run main.py bootstrap --env=prod

# 后续部署：显式应用已审查的迁移
uv run main.py migrate --env=prod

# 启动：自动迁移、校验结构并补齐基础数据
uv run main.py run --env=prod
```

生产启动会在 Redis 启动锁下自动应用已提交的 Alembic 迁移、创建缺失结构并补齐稳定基础数据，然后校验数据库结构。`bootstrap` 和 `migrate` 仍可在部署阶段用于显式预执行；`reset` 会删除数据，不应作为启动修复手段。

开发模型变更后运行 `uv run main.py revision --env=dev`，审查生成的 `app/alembic/versions/` 脚本及数据影响，再显式执行迁移。

## 数据与验证

通用上传和知识库原件分别位于 `storage/upload/`、`storage/knowledge/`；Chroma 和 BM25 目录由 `CHROMA_PERSIST_DIR`、`BM25_INDEX_DIR` 配置。关系库、文件和索引必须一起考虑恢复，见[备份与恢复](../docs/BACKUP_AND_RESTORE.md)。

```bash
# 安装依赖后，按改动范围选择
.venv/bin/python -m pytest tests/test_api_module_ai.py -q
.venv/bin/python -m pytest tests/plugin/module_ai -q
.venv/bin/ruff check app tests --no-fix --output-format concise
```

测试默认使用临时 SQLite 和模拟 Redis，只证明测试环境行为，不能替代 MySQL 或 PostgreSQL 验收。跨层或真实依赖验收见[测试与验收](../docs/TESTING.md)。新增业务可运行 `uv run --no-sync main.py generate-module --module business --resource supplier --title 供应商`，默认预览，添加 `--apply` 写入；见[模块生成器](../docs/MODULE_GENERATOR.md)和[标准模块模板](../docs/STANDARD_MODULE_TEMPLATE.md)。
