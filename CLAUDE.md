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
npm run test              # Run all 316 tests
npm run test:watch        # Watch mode

# Backend (Pytest)
npm run test:server       # Run all 233 tests

# Run single test file
npx vitest run src/stores/__tests__/menu.test.ts
cd server && python -m pytest tests/test_backup.py -v
```

### Build
```bash
npm run build  # Outputs to dist/
```

## Architecture Overview

This is a **configuration-driven** dynamic data management platform. Unlike traditional CRUD apps, you do **not** create new Vue pages or database tables for new business entities. Instead, you define a `PageConfig` (field schema) and a `Menu` entry, and the system auto-generates the UI and API endpoints.

### Key Architectural Patterns

1.  **Single-Table Dynamic Data**: All business data is stored in `dynamic_data` table using PostgreSQL JSONB. The `collection` field (derived from `pageId`) separates entities. This eliminates the need for schema migrations when adding new entities or fields.

2.  **Frontend Dynamic Rendering**: `src/views/dynamic/DynamicPage.vue` is the only page for business data. It reads `pageId` from the route, fetches the `PageConfig` schema, and renders `DynamicForm` (for editing) and `DataTable` (for listing) based on field definitions.

3.  **Dynamic Route Generation**: `src/router/dynamicRoutes.ts` reads the `menus` table at runtime and generates Vue Router routes pointing to `DynamicPage.vue`.

4.  **Field-Driven Logic**: Behavior is determined by `FieldConfig` in `page_configs.fields`. For example:
    *   `controlType: 'relation'` triggers M:N relationship handling via `data_relations` table.
    *   `controlType: 'autoSequence'` triggers sequence generation logic in the store.
    *   `controlType: 'reference'` creates a 1:N link and inherits parent fields into child records.

### Database Design

*   **`dynamic_data`**: The core table. `data` column holds all fields as JSONB.
*   **`data_relations`**: Stores M:N relationships. Queried by `(collection, record_id, field_name)`.
*   **`page_configs`**: The schema definition. `fields` JSONB column drives the entire UI.
*   **`menus`**: Tree structure with `roles` JSONB for RBAC.

### Backend Structure

*   `app.py`: Registers blueprints. **Order matters**: `dynamic_bp` (catch-all `/<collection>`) must be registered last to avoid shadowing specific routes.
*   `routes/dynamic.py`: The generic CRUD handler. It inspects `page_configs` to validate data and manage relations.
*   `utils/script_runner.py`: Sandboxed Python execution for validation/export scripts.

### Frontend Structure

*   `src/stores/pageConfig.ts`: The central store. Contains critical logic for:
    *   Resolving relation/reference/quote field imports.
    *   Generating auto-sequence values.
    *   Batch data operations.
*   `src/components/dynamic-form/controls/`: 15 field control components. Mapped by `controlType`.

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
3.  Update `FieldConfig` type in `src/types/index.ts`.
4.  **Backend**: If validation is needed, update `routes/dynamic.py` validation logic.

## Key Technical Details

*   **Authentication**: JWT in `Authorization: Bearer <token>` header. Middleware in `auth.py` decorator.
*   **API Proxy**: Vite config proxies `/api` to backend port 3001.
*   **Default Credentials**: admin / admin123 (set in `server/seed_data.py`).
*   **Database Config**: `server/config.py`.
*   **Testing**: Frontend uses Vitest with `@/` path alias mocking. Backend uses Pytest with `unittest.mock` for database patching.