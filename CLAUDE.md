# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Development
```bash
# Install dependencies
npm install
pip install flask flask-cors psycopg2-binary PyJWT pytest

# Initialize database (runs DDL and seeds default admin)
cd server && python init_db.py

# Start development (both frontend and backend)
npm run dev:all
# Frontend: http://localhost:5173
# Backend: http://localhost:3001

# Run individually
npm run server  # Backend only (port 3001)
npm run dev     # Frontend only (port 5173, proxies /api to :3001)
```

### Testing
```bash
# Frontend (Vitest)
npm run test              # Run all frontend tests
npm run test:watch        # Watch mode

# Backend (Pytest)
npm run test:server       # Run all backend tests

# Both frontend and backend
npm run test:all

# Run single test file
npx vitest run src/stores/__tests__/menu.test.ts
cd server && python -m pytest tests/test_backup.py -v
```

### Build
```bash
npm run build  # vue-tsc type check + vite build → dist/
```

## Architecture Overview

This is a **configuration-driven** dynamic data management platform. Unlike traditional CRUD apps, you do **not** create new Vue pages or database tables for new business entities. Instead, you define a `PageConfig` (field schema) and a `Menu` entry, and the system auto-generates the UI and API endpoints.

**Tech stack**: Vue 3 + TypeScript + Element Plus + Pinia (frontend), Python Flask + psycopg2 (backend), PostgreSQL with JSONB (database).

### Key Architectural Patterns

1.  **Single-Table Dynamic Data**: All business data is stored in `dynamic_data` table using PostgreSQL JSONB. The `collection` field (derived from `pageId`) separates entities. This eliminates the need for schema migrations when adding new entities or fields.

2.  **Frontend Dynamic Rendering**: `src/views/dynamic/DynamicPage.vue` is the only page for business data. It reads `pageId` from the route, fetches the `PageConfig` schema, and renders three view modes based on field definitions:
    *   **Table view** (default): `DataTable` + `DynamicForm` dialog
    *   **Kanban view**: `KanbanBoard` with configurable group/card fields
    *   **Excel view**: `ExcelView` using Univer.js spreadsheet

3.  **Dynamic Route Generation**: `src/router/dynamicRoutes.ts` reads the `menus` table at runtime and generates Vue Router routes pointing to `DynamicPage.vue`.

4.  **Field-Driven Logic**: Behavior is determined by `FieldConfig` in `page_configs.fields`. For example:
    *   `controlType: 'relation'` triggers M:N relationship handling via `data_relations` table.
    *   `controlType: 'autoSequence'` triggers sequence generation logic in the store.
    *   `controlType: 'reference'` creates a 1:N link and inherits parent fields into child records.
    *   `controlType: 'quoteSelect'` creates a single-direction multi-select reference.

### All Control Types

Defined in `src/types/field.ts`. The `controlType` value determines which component renders the field:

| Type | Description |
|------|-------------|
| `text`, `textarea`, `richText`, `number` | Input controls |
| `select`, `multiSelect`, `radio`, `checkbox` | Selection controls |
| `date`, `datetime` | Date/time pickers |
| `file`, `image` | Upload controls |
| `relation` | M:N bidirectional association via `data_relations` table |
| `reference` | 1:N parent-child with field inheritance |
| `quoteSelect` | Single-direction multi-select quote |
| `autoSequence` | Auto-incrementing ID (e.g., "IC-001") |
| `autoTimestamp` | Auto-filled on create/update |

### Database Design

*   **`dynamic_data`**: The core table. `data` column holds all fields as JSONB. Has `branch_id` for version isolation.
*   **`data_relations`**: Stores M:N relationships. Queried by `(collection, record_id, field_name)`.
*   **`page_configs`**: The schema definition. `fields` JSONB column drives the entire UI.
*   **`menus`**: Tree structure with `roles` JSONB for RBAC.
*   **`users`**: Accounts with `role` (admin / developer / guest).
*   Other tables: `export_scripts`, `validation_scripts`, `etl_tasks`, `etl_logs`, `api_keys`, `operation_logs`, `backups`, `backup_settings`, `ai_settings`, `collection_versions`, `version_snapshots`, `dashboards`, `record_comments`.

### Backend Structure

*   `app.py`: Registers 22 blueprints. **Order matters**: `dynamic_bp` (catch-all `/<collection>`) must be registered last to avoid shadowing specific routes.
*   `routes/dynamic.py`: The generic CRUD handler. Supports pagination (`page`, `pageSize`, `all=true`), filtering (`q` for MongoDB-style query, `keyword` for full-text search). Validates primary key uniqueness and manages relations.
*   `utils/script_runner.py`: Sandboxed Python execution for validation/export scripts.
*   `utils/auth.py`: JWT decorators — `login_required`, `write_required` (blocks guest), `admin_required`, `api_key_required`.
*   `utils/db.py`: `psycopg2.pool.SimpleConnectionPool` (1–10 connections). Use `get_db()` context manager.

**Reserved collection paths** (cannot be used as dynamic data collection names): `menus`, `pageConfigs`, `relations`, `auth`, `users`, `operationLogs`, `backups`, `exportScripts`, `apiKeys`, `validationScripts`, `etlTasks`, `relation-graph`, `query`, `comments`, `timeline`, `dashboards`, `notifications`, `triggerRules`, `ai`, `versions`.

### Frontend Structure

*   `src/stores/pageConfig.ts`: The central store. Contains critical logic for:
    *   Resolving relation/reference/quote field imports.
    *   Generating auto-sequence values.
    *   Batch data operations.
*   `src/stores/menu.ts`: Menu tree with shallowRef caching + role-based filtering.
*   `src/stores/auth.ts`: JWT token management. localStorage keys prefixed `check-manage:`.
*   `src/components/dynamic-form/controls/`: Field control components. Mapped by `controlType`.
*   `src/api/`: Axios-based API layer. Base URL `/api`, 30s timeout. Request interceptor injects Bearer token.

### API Proxy

Vite proxies `/api` to backend port 3001 **with path rewrite**: frontend calls `/api/menus` → backend receives `/menus`. This is why backend routes don't have an `/api` prefix.

### User Roles

*   **admin**: Full access to all features and admin pages.
*   **developer**: Read/write data, but no admin page access.
*   **guest**: Read-only access. `write_required` decorator blocks mutations.

## Development Workflow

### Adding a New Business Entity (e.g., "Products")

**Do NOT** create a new Vue file or SQL table.

1.  Insert a row into `page_configs`:
    ```sql
    INSERT INTO page_configs (id, name, fields)
    VALUES ('page-products', '产品管理', '[{"fieldName": "name", "label": "产品名称", "controlType": "text", ...}]');
    ```
2.  Insert a row into `menus`:
    ```sql
    INSERT INTO menus (id, name, page_id, path, parent_id, "order")
    VALUES ('menu-products', '产品管理', 'page-products', '/products', NULL, 1);
    ```
3.  Done. The system creates the route `/products`, the API `GET/POST /products`, and the UI.

### Adding a New Field Control Type

1.  **Frontend**: Create `src/components/dynamic-form/controls/NewControl.vue`.
2.  Register it in `DynamicForm.vue` and `DataTable.vue` column rendering.
3.  Update `ControlType` in `src/types/field.ts` and re-export from `src/types/index.ts`.
4.  **Backend**: If validation is needed, update `routes/dynamic.py` validation logic.

### Script Runner Sandbox

Validation and export scripts execute in a sandboxed Python environment (`utils/script_runner.py`):
*   **Timeout**: 60s for row-level scripts, 300s for collection-level scripts.
*   **Forbidden**: `import`, `eval`, `exec`, `open`, `getattr`, `type`, etc.
*   **Pre-injected modules**: `json`, `csv`, `io`, `re`, `math`, `collections`, `xml.etree.ElementTree`, `datetime`, `pandas` (as `pd`), `numpy` (as `np`).
*   **No `print()`** — scripts communicate via return values.

## Key Technical Details

*   **Authentication**: JWT in `Authorization: Bearer <token>` header. Middleware decorators in `server/utils/auth.py`.
*   **Default Credentials**: admin / admin123 (set in `server/seed_data.py`).
*   **Database Config**: `server/config.py` — PostgreSQL on localhost:5432, database `casemanage`.
*   **TypeScript**: Strict mode enabled (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/` → `src/`.
*   **SCSS**: Global variables via `src/assets/styles/variables.scss` (auto-imported in all SCSS).
*   **Production**: `start.sh` builds frontend, starts backend + reverse proxy on port 8080.

## Testing Patterns

Frontend tests: `src/**/__tests__/**/*.test.ts` with jsdom environment. Setup file: `src/test/setup.ts` (provides localStorage mock).

### ResizeObserver Polyfill
jsdom environment lacks ResizeObserver. Add this polyfill in test files when components use Element Plus or resize-dependent features:
```typescript
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})
```

### Element Plus Component Stubs
For isolated component testing, stub Element Plus components with minimal implementations:
```typescript
const stubs = {
  'el-checkbox': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('change', $event.target.checked)" />`,
    props: ['modelValue'],
    emits: ['change'],
  },
  'el-button': {
    template: `<button @click="$emit('click')"><slot /></button>`,
    emits: ['click'],
  },
}
```

### Deep Equality Assertions
Use `toStrictEqual` for object comparisons (not `toBe`):
```typescript
// ❌ Wrong - fails for objects
expect(state.sourceVersion).toBe(version)
// ✅ Correct - deep equality
expect(state.sourceVersion).toStrictEqual(version)
```

## Composables Pattern

Use Vue 3 composables for reusable state management. Key patterns:
- Return reactive state and computed properties
- Provide action functions that modify state
- Use `reactive()` for complex state objects
- Use `Set`/`Map` for collection state (more intuitive than arrays for toggle operations)
- Distinguish between data existence (`hasDiff`) and user action state (`hasSelection`)