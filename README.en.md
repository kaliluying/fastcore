# FastCore

FastCore is an enterprise backend scaffold for single-organization applications, built with FastAPI and accompanied by a Vue 3 administration frontend. It includes authentication, role permissions, menus, audit logs and file management, plus built-in AI chat, knowledge bases, document retrieval, usage limits and quality evaluation.

FastCore is a secondary development of the predecessor [FastapiAdmin](https://github.com/fastapiadmin/FastapiAdmin) project. It inherits and extends the backend and administration capabilities while reorganizing the brand, directories, configuration, database and product experience. FastapiAdmin is retained only as the project origin name, not as a runtime compatibility identifier.

[中文](README.md) · [Documentation index](docs/README.md) · [Architecture](docs/ARCHITECTURE_AND_DEVELOPMENT.md) · [Business module template](docs/STANDARD_MODULE_TEMPLATE.md)

## Structure

| Location                                            | Purpose                                                                                               |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [backend/](backend/README.md)                       | FastAPI, SQLAlchemy, Alembic, authentication, users, roles, menus, logs, files and health checks      |
| [frontend/](frontend/README.md)                     | Vue 3, TypeScript, Vite, Pinia and Element Plus administration frontend                               |
| `backend/app/plugin/module_ai/`                     | Chat, knowledge bases, memory, model configuration, usage limits, feedback and retrieval evaluation   |
| [Module template](docs/STANDARD_MODULE_TEMPLATE.md) | A development-only category example covering data constraints, permissions, navigation and acceptance |
| [Backup and restore](docs/BACKUP_AND_RESTORE.md)    | Offline backup and recovery of the database, private files, Chroma and BM25 indexes                   |

AI is included by default and assembled explicitly; copied projects can remove the complete module and its dependencies. See the [module development guide](docs/MODULE_DEVELOPMENT.md). The scaffold uses a single-organization permission model.

## Local development

Use Python 3.12+, `uv`, Node.js 22.22.2+ (22.x), 24.15+ (24.x), or 26+, and the pnpm version declared in `frontend/package.json` through Corepack. Prepare MySQL or PostgreSQL and Redis. Both are officially supported runtime databases; SQLite is reserved for automated tests and isolated acceptance runs. See the [architecture guide](docs/ARCHITECTURE_AND_DEVELOPMENT.md).

```bash
git clone git@github.com:kaliluying/fastcore.git
cd fastcore
cp backend/env/.env.dev.example backend/env/.env.dev
```

Fill in database, Redis and secret settings before starting. Check the frontend API proxy target (AI chat also uses HTTP/SSE) in `.env` and `.env.development` against the actual backend configuration.

```bash
# Terminal 1, from the repository root
cd backend
uv sync
uv run main.py run --env=dev
```

```bash
# Terminal 2, from the repository root
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

Development and production startup both migrate, initialize and validate the selected database under a Redis lock. `bootstrap --env=prod` and `migrate --env=prod` remain available for explicit deployment preparation. See the [backend guide](backend/README.md). AI dependencies are included in `uv sync`; external model services and OCR tools require separate configuration.

## Development and maintenance

New business modules can start from the [module generator](docs/MODULE_GENERATOR.md) or the [module template](docs/STANDARD_MODULE_TEMPLATE.md). Use the [architecture guide](docs/ARCHITECTURE_AND_DEVELOPMENT.md) for call chains and the [testing guide](docs/TESTING.md) for commands and acceptance boundaries. `main` is the default and current delivery branch. Detailed documentation is maintained primarily in Chinese.

All project identifiers use FastCore: the default database is `fastcore`, Redis brand prefixes are `fastcore:` / `fastcore-cache:`, logs use `backend/logs/fastcore.log`, the CLI object is `fastcore_cli`, and design documentation lives in `design-system/fastcore-quiet-operations/`. The local directory is named `FastCore`. Existing installations must back up and migrate their database and update environment settings before activating the new version; old brand aliases are not retained.

Current source, dependency declarations and effective environment settings take precedence over historical implementation reports. Collaboration rules are in [AGENTS.md](AGENTS.md).
