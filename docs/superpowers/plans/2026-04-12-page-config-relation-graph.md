# Page Config Relation Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add interactive relation graph visualization to page config manager using Vue Flow

**Architecture:** Backend provides recursive BFS scan API returning nodes/edges, frontend renders with Vue Flow, color-coded by relation type, supports node click navigation

**Tech Stack:** Python Flask (backend), Vue 3 + Vue Flow (frontend), PostgreSQL

---

## File Structure

**Backend:**
- `server/utils/page_config_relations.py` (create) - BFS recursive scan logic
- `server/routes/page_configs.py` (modify) - Add GET /page-configs/:id/relations route
- `server/tests/test_page_config_relations.py` (create) - Backend unit tests

**Frontend:**
- `src/components/PageConfigRelationGraph.vue` (create) - Vue Flow graph component
- `src/views/admin/PageConfigManager.vue` (modify) - Add 5th tab
- `src/api/pageConfigs.ts` (modify) - Add getRelations API call
- `src/components/__tests__/PageConfigRelationGraph.test.ts` (create) - Frontend component tests

---

### Task 1: Backend - Create Relation Scan Utility

**Files:**
- Create: `server/utils/page_config_relations.py`
- Test: `server/tests/test_page_config_relations.py`

- [ ] **Step 1: Write failing test for single page with no relations**

```python
# server/tests/test_page_config_relations.py
import pytest
from utils.page_config_relations import get_page_config_relations
from db import get_db

def test_single_page_no_relations():
    """测试无关联的单个页面"""
    # Setup: Create simple page config with no relation fields
    with get_db() as conn:
        cur = conn.cursor()

        # Create test page config
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-simple', '测试页面', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-simple')

    assert 'nodes' in result
    assert 'edges' in result
    assert len(result['nodes']) == 1
    assert result['nodes'][0]['id'] == 'page-test-simple'
    assert len(result['edges']) == 0

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id = %s', ('page-test-simple',))
        conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_page_config_relations.py::test_single_page_no_relations -v`
Expected: FAIL with "module not found" or "function not defined"

- [ ] **Step 3: Create minimal implementation for single page**

```python
# server/utils/page_config_relations.py
"""
页面配置关联关系扫描工具

使用BFS递归扫描页面配置之间的关联关系
"""

from db import get_db
import psycopg2.extras

def get_page_config_relations(page_id: str, max_depth: int = 3):
    """
    获取页面配置的关联关系

    Args:
        page_id: 页面配置ID
        max_depth: 最大递归深度（默认3层）

    Returns:
        dict: {nodes: [...], edges: [...]}
    """
    visited = set()
    nodes = []
    edges = []

    # BFS队列
    queue = [(page_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)

        if current_id in visited or depth > max_depth:
            continue

        visited.add(current_id)

        # 获取页面配置
        with get_db() as conn:
            cur = conn.cursor()

            cur.execute(
                'SELECT id, name, fields FROM page_configs WHERE id = %s',
                (current_id,)
            )
            page_config = cur.fetchone()

            if not page_config:
                continue

            nodes.append({
                'id': current_id,
                'name': page_config[1],
                'fields': len(page_config[2]) if page_config[2] else 0
            })

    return {'nodes': nodes, 'edges': edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_page_config_relations.py::test_single_page_no_relations -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/page_config_relations.py server/tests/test_page_config_relations.py
git commit -m "feat: add page config relation scan utility (single page support)"
```

---

### Task 2: Backend - Add Field Relation Extraction

**Files:**
- Modify: `server/utils/page_config_relations.py:30-50`
- Modify: `server/tests/test_page_config_relations.py`

- [ ] **Step 1: Write failing test for two pages with relation**

```python
# server/tests/test_page_config_relations.py (add after first test)
def test_two_pages_with_relation():
    """测试两个页面通过relation字段关联"""
    with get_db() as conn:
        cur = conn.cursor()

        # Create page A with relation to page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedB',
                    'label': '关联B',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'page-test-b',
                        'displayField': 'name',
                        'targetField': 'relatedA'
                    }
                }
            ]))
        )

        # Create page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-b', '页面B', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-a', max_depth=2)

    assert len(result['nodes']) == 2
    assert len(result['edges']) == 1

    edge = result['edges'][0]
    assert edge['source'] == 'page-test-a'
    assert edge['target'] == 'page-test-b'
    assert edge['type'] == 'relation'
    assert edge['field'] == 'relatedB'
    assert edge['label'] == '关联B'

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-a', 'page-test-b'))
        conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_page_config_relations.py::test_two_pages_with_relation -v`
Expected: FAIL with "edges is empty" or similar

- [ ] **Step 3: Implement field extraction logic**

```python
# server/utils/page_config_relations.py (modify after line 30)

def extract_target_collection(field: dict) -> str | None:
    """根据字段类型提取目标集合ID"""

    control_type = field.get('controlType')

    if control_type == 'relation':
        config = field.get('relationConfig', {})
        return config.get('targetCollection')

    elif control_type == 'reference':
        config = field.get('referenceConfig', {})
        return config.get('targetCollection')

    elif control_type == 'quoteSelect':
        config = field.get('quoteConfig', {})
        return config.get('targetCollection')

    return None

def get_page_config_relations(page_id: str, max_depth: int = 3):
    """获取页面配置的关联关系"""

    visited = set()
    nodes = []
    edges = []

    queue = [(page_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)

        if current_id in visited or depth > max_depth:
            continue

        visited.add(current_id)

        with get_db() as conn:
            cur = conn.cursor()

            cur.execute(
                'SELECT id, name, fields FROM page_configs WHERE id = %s',
                (current_id,)
            )
            page_config = cur.fetchone()

            if not page_config:
                continue

            nodes.append({
                'id': current_id,
                'name': page_config[1],
                'fields': len(page_config[2]) if page_config[2] else 0
            })

            # 扫描字段关联
            fields = page_config[2] or []
            for field in fields:
                target = extract_target_collection(field)

                if target:
                    edges.append({
                        'source': current_id,
                        'target': target,
                        'type': field['controlType'],
                        'field': field['fieldName'],
                        'label': field.get('label', field['fieldName'])
                    })

                    if target not in visited:
                        queue.append((target, depth + 1))

    return {'nodes': nodes, 'edges': edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_page_config_relations.py::test_two_pages_with_relation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/page_config_relations.py server/tests/test_page_config_relations.py
git commit -m "feat: extract relation/reference/quoteSelect field targets"
```

---

### Task 3: Backend - Add Circular Reference Handling

**Files:**
- Modify: `server/utils/page_config_relations.py`
- Modify: `server/tests/test_page_config_relations.py`

- [ ] **Step 1: Write failing test for circular reference**

```python
# server/tests/test_page_config_relations.py (add)
def test_circular_reference():
    """测试循环引用 A → B → A"""
    with get_db() as conn:
        cur = conn.cursor()

        # A relates to B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-circ-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedB',
                    'label': '关联B',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'page-test-circ-b',
                        'displayField': 'name',
                        'targetField': 'relatedA'
                    }
                }
            ]))
        )

        # B relates to A (circular)
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-circ-b', '页面B', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedA',
                    'label': '关联A',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'page-test-circ-a',
                        'displayField': 'name',
                        'targetField': 'relatedB'
                    }
                }
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-circ-a')

    # Should not infinite loop
    # Should have exactly 2 nodes (not duplicated)
    assert len(result['nodes']) == 2

    # Should have 2 edges (A→B and B→A)
    assert len(result['edges']) == 2

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-circ-a', 'page-test-circ-b'))
        conn.commit()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_page_config_relations.py::test_circular_reference -v`
Expected: PASS (visited Set already handles this)

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_page_config_relations.py
git commit -m "test: verify circular reference handling"
```

---

### Task 4: Backend - Add API Route

**Files:**
- Modify: `server/routes/page_configs.py`
- Test: Manual API test

- [ ] **Step 1: Find existing page_configs route structure**

Run: `grep -n "page_configs_bp" server/routes/page_configs.py | head -5`
Check structure and route pattern

- [ ] **Step 2: Add relations route**

```python
# server/routes/page_configs.py (add new route)
from utils.page_config_relations import get_page_config_relations

@page_configs_bp.route('/<page_id>/relations', methods=['GET'])
def get_relations(page_id):
    """获取页面配置的关联关系图谱"""
    try:
        depth = int(request.args.get('depth', 3))

        result = get_page_config_relations(page_id, max_depth=depth)

        if len(result['nodes']) == 0:
            return {'error': '页面配置不存在'}, 404

        if len(result['nodes']) > 50:
            return {
                'error': '关联节点过多（>50），建议减少递归深度',
                'hint': '请使用depth参数限制层级'
            }, 400

        return result

    except Exception as e:
        return {'error': str(e)}, 500
```

- [ ] **Step 3: Test API manually**

Run: Start backend server and test:
```bash
curl http://localhost:3001/page-configs/page-inspection-case/relations
```
Expected: JSON with nodes and edges array

- [ ] **Step 4: Commit**

```bash
git add server/routes/page_configs.py
git commit -m "feat: add GET /page-configs/:id/relations API route"
```

---

### Task 5: Frontend - Install Vue Flow Dependencies

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Install Vue Flow packages**

Run: `npm install @vue-flow/core @vue-flow/background @vue-flow/controls`

- [ ] **Step 2: Verify installation**

Run: `npm list @vue-flow/core`
Expected: Shows installed version

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json
git commit -m "feat: add Vue Flow dependencies for graph visualization"
```

---

### Task 6: Frontend - Create Relation Graph Component

**Files:**
- Create: `src/components/PageConfigRelationGraph.vue`

- [ ] **Step 1: Create basic Vue Flow component structure**

```vue
<!-- src/components/PageConfigRelationGraph.vue -->
<template>
  <div class="relation-graph-container">
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>正在加载关系图谱...</span>
    </div>

    <el-empty
      v-if="!loading && nodes.length === 1 && edges.length === 0"
      description="当前页面配置无关联关系"
    />

    <VueFlow
      v-if="!loading && nodes.length > 0"
      :nodes="nodes"
      :edges="edges"
      :default-viewport="{ zoom: 1.0 }"
      :min-zoom="0.2"
      :max-zoom="4"
      fit-view-on-init
      @node-click="handleNodeClick"
    >
      <Background />
      <Controls />

      <template #node-custom="{ data }">
        <div class="custom-node">
          <div class="node-header">{{ data.name }}</div>
          <div class="node-body">
            <div class="node-meta">{{ data.fields }} 个字段</div>
            <div class="node-id">{{ data.id }}</div>
          </div>
        </div>
      </template>
    </VueFlow>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps<{
  pageId: string
}>()

const emit = defineEmits<{
  navigate: [targetPageId: string]
}>()

const loading = ref(false)
const nodes = ref<any[]>([])
const edges = ref<any[]>([])

function getEdgeStyle(type: string) {
  switch (type) {
    case 'relation':
      return { stroke: '#409EFF', strokeWidth: 2 }
    case 'reference':
      return { stroke: '#67C23A', strokeWidth: 2 }
    case 'quoteSelect':
      return { stroke: '#E6A23C', strokeWidth: 2 }
    default:
      return { stroke: '#909399', strokeWidth: 2 }
  }
}

async function loadRelations() {
  loading.value = true

  try {
    // TODO: Call API in next task
    nodes.value = []
    edges.value = []
  } catch (error) {
    ElMessage.error('加载关系图谱失败')
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

function handleNodeClick(event: { node: any }) {
  const targetPageId = event.node.id

  if (targetPageId === props.pageId) {
    return
  }

  emit('navigate', targetPageId)
}

onMounted(() => {
  loadRelations()
})

watch(() => props.pageId, () => {
  loadRelations()
})
</script>

<style scoped>
.relation-graph-container {
  width: 100%;
  height: 600px;
  border: 1px solid #E4E7ED;
  border-radius: 4px;
  position: relative;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: #606266;
}

.custom-node {
  background: #fff;
  border: 2px solid #409EFF;
  border-radius: 8px;
  padding: 12px;
  min-width: 120px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.node-header {
  font-weight: bold;
  font-size: 14px;
  color: #303133;
  margin-bottom: 8px;
}

.node-body {
  font-size: 12px;
  color: #606266;
}

.node-meta {
  margin-bottom: 4px;
}

.node-id {
  color: #909399;
  font-size: 11px;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/PageConfigRelationGraph.vue
git commit -m "feat: create PageConfigRelationGraph component with Vue Flow"
```

---

### Task 7: Frontend - Add API Call Function

**Files:**
- Modify: `src/api/pageConfigs.ts`
- Modify: `src/components/PageConfigRelationGraph.vue`

- [ ] **Step 1: Add getRelations API function**

```typescript
// src/api/pageConfigs.ts (add)
import { get } from '@/utils/request'

export interface RelationNode {
  id: string
  name: string
  fields: number
}

export interface RelationEdge {
  source: string
  target: string
  type: string
  field: string
  label: string
}

export interface RelationGraph {
  nodes: RelationNode[]
  edges: RelationEdge[]
}

export function getPageConfigRelations(pageId: string, depth?: number) {
  const params: Record<string, any> = {}
  if (depth) params.depth = depth

  return get<RelationGraph>(`/page-configs/${pageId}/relations`, params)
}
```

- [ ] **Step 2: Update component to use API**

```typescript
// src/components/PageConfigRelationGraph.vue (modify loadRelations)
import { getPageConfigRelations } from '@/api/pageConfigs'

async function loadRelations() {
  loading.value = true

  try {
    const result = await getPageConfigRelations(props.pageId, 3)

    // Convert to Vue Flow format
    nodes.value = result.nodes.map(n => ({
      id: n.id,
      type: 'custom',
      position: { x: 0, y: 0 },
      data: {
        name: n.name,
        fields: n.fields,
        id: n.id
      }
    }))

    edges.value = result.edges.map(e => ({
      id: `${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      label: e.label,
      type: 'smoothstep',
      style: getEdgeStyle(e.type),
      animated: e.type === 'relation'
    }))

  } catch (error: any) {
    const msg = error?.response?.data?.error || '加载关系图谱失败'
    ElMessage.error(msg)
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}
```

- [ ] **Step 3: Test manually**

Start dev server, open page config manager, select a page, switch to relation graph tab
Expected: Graph renders with nodes and colored edges

- [ ] **Step 4: Commit**

```bash
git add src/api/pageConfigs.ts src/components/PageConfigRelationGraph.vue
git commit -m "feat: integrate API call into relation graph component"
```

---

### Task 8: Integration - Add Tab to PageConfigManager

**Files:**
- Modify: `src/views/admin/PageConfigManager.vue`

- [ ] **Step 1: Import relation graph component**

```typescript
// src/views/admin/PageConfigManager.vue (script section)
import PageConfigRelationGraph from '@/components/PageConfigRelationGraph.vue'
```

- [ ] **Step 2: Add relation graph tab**

```vue
<!-- src/views/admin/PageConfigManager.vue (after line 401) -->
<el-tab-pane label="关系图谱" name="relations">
  <PageConfigRelationGraph
    :page-id="currentPageId"
    @navigate="handleNavigateToPage"
  />
</el-tab-pane>
```

- [ ] **Step 3: Add navigation handler**

```typescript
// src/views/admin/PageConfigManager.vue (script section)
function handleNavigateToPage(targetPageId: string) {
  const targetConfig = pageConfigs.value.find(c => c.id === targetPageId)

  if (targetConfig) {
    handleSelect(targetConfig)
  } else {
    ElMessage.warning('目标页面配置不存在')
  }
}
```

- [ ] **Step 4: Test manually**

Open page config manager, select page with relations, click "关系图谱" tab
Expected: Shows graph, clicking node navigates to that page

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/PageConfigManager.vue
git commit -m "feat: integrate relation graph tab into page config manager"
```

---

### Task 9: Frontend Tests - Component Unit Tests

**Files:**
- Create: `src/components/__tests__/PageConfigRelationGraph.test.ts`

- [ ] **Step 1: Write component render test**

```typescript
// src/components/__tests__/PageConfigRelationGraph.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import PageConfigRelationGraph from '../PageConfigRelationGraph.vue'

// Mock API
vi.mock('@/api/pageConfigs', () => ({
  getPageConfigRelations: vi.fn()
}))

describe('PageConfigRelationGraph', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading state initially', () => {
    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-test' }
    })

    expect(wrapper.find('.loading-state').exists()).toBe(true)
  })

  it('renders nodes after loading', async () => {
    const { getPageConfigRelations } = await import('@/api/pageConfigs')

    vi.mocked(getPageConfigRelations).mockResolvedValue({
      nodes: [
        { id: 'page-a', name: '页面A', fields: 5 }
      ],
      edges: []
    })

    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-a' }
    })

    await new Promise(resolve => setTimeout(resolve, 100))

    expect(wrapper.vm.nodes.length).toBe(1)
  })
})
```

- [ ] **Step 2: Run test**

Run: `npm run test src/components/__tests__/PageConfigRelationGraph.test.ts`
Expected: Tests pass

- [ ] **Step 3: Commit**

```bash
git add src/components/__tests__/PageConfigRelationGraph.test.ts
git commit -m "test: add basic component tests for relation graph"
```

---

### Task 10: Manual Testing Checklist

**Files:**
- None (manual testing)

- [ ] **Step 1: Test basic rendering**

Manual checklist:
- Open page config manager
- Select page with relations (inspection-case)
- Click "关系图谱" tab
- Verify nodes display correctly
- Verify edges colored correctly

- [ ] **Step 2: Test node navigation**

- Click on related node (inspection-plan)
- Verify switches to that page config
- Verify graph reloads for new page

- [ ] **Step 3: Test circular reference**

- Create test pages with circular relation
- Verify graph doesn't crash
- Verify nodes not duplicated

- [ ] **Step 4: Test edge cases**

- Select page with no relations → shows empty state
- Select page with >50 nodes → shows error message
- Test depth limit (3 layers)

- [ ] **Step 5: Create final documentation commit**

```bash
git commit --allow-empty -m "test: manual testing completed for page config relation graph

Tested:
- Basic rendering with nodes and edges
- Node click navigation
- Circular reference handling
- Empty state display
- Performance with multiple nodes

All features working as expected."
```

---

## Verification Checklist

After implementation, verify:

1. **Backend API works:**
   - GET /page-configs/:id/relations returns correct JSON
   - Circular references handled correctly
   - Depth limit enforced

2. **Frontend component renders:**
   - Vue Flow displays nodes and edges
   - Colors distinguish relation types
   - Click navigation works

3. **Integration complete:**
   - Tab added to PageConfigManager
   - Navigate between pages works
   - Empty state displays correctly

4. **Tests pass:**
   - Backend unit tests: `python -m pytest server/tests/test_page_config_relations.py -v`
   - Frontend component tests: `npm run test`

---

## Success Criteria

- ✅ Backend API returns relation graph data
- ✅ Frontend renders interactive graph with Vue Flow
- ✅ Node click navigates to page config
- ✅ Colors distinguish relation/reference/quoteSelect
- ✅ Circular references handled without crash
- ✅ Empty state shows for isolated pages
- ✅ Tests pass (backend + frontend)