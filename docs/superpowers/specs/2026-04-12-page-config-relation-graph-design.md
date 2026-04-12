# 页面配置关系可视化设计文档

> **日期**: 2026-04-12
> **作者**: Claude Code
> **目标**: 在页面配置管理中增加关系图谱功能，以交互式节点网络图展示页面配置之间的关联关系

---

## 背景

Check-Manage系统采用配置驱动的动态数据管理架构，页面配置通过字段定义数据结构。字段支持三种关联类型：

- **relation**: M:N双向关联（通过data_relations表）
- **reference**: 1:N父子引用（字段继承）
- **quoteSelect**: 单向多选引用

当前页面配置管理界面（PageConfigManager.vue）有4个Tab：基本信息、视图配置、字段配置、删除绑定。配置时难以直观理解页面之间的关联结构，需要手动查阅多个页面配置。

**需求**: 在配置页面时以图形化方式查看关联关系，便于理解整体架构。

---

## 设计决策

### 使用场景
**配置时查看关联结构** - 在编辑页面配置时查看当前页面与其他页面的关联关系。

### 呈现方式
**节点网络图** - 使用Vue Flow实现交互式图谱，支持拖拽、缩放、点击跳转。

### 节点含义
**节点=页面配置** - 每个节点代表一个页面配置（如inspection-case），连线表示页面间关联关系。

### 关联类型区分
**颜色区分** - 不同颜色区分三种关联类型：
- relation: 蓝色 (#409EFF)
- reference: 绿色 (#67C23A)
- quoteSelect: 橙色 (#E6A23C)

### 展示范围
**多层关联递归展示** - BFS递归扫描关联页面，展示完整关系链。

---

## 架构设计

### 新增组件结构

**前端组件：**
```
src/components/PageConfigRelationGraph.vue  # 关系图谱组件
src/views/admin/PageConfigManager.vue       # 新增第5个Tab
```

**后端API：**
```
server/routes/page_configs.py  # 新增路由
GET /page-configs/:id/relations  # 返回递归关联数据
```

**依赖库：**
```
npm install @vue-flow/core @vue-flow/background @vue-flow/controls
```

---

## 数据流设计

```
用户点击"关系图谱"Tab
  → 前端调用 GET /page-configs/:id/relations
  → 后端BFS递归扫描关联页面
  → 返回 {nodes, edges} JSON
  → 前端转换为Vue Flow格式
  → Vue Flow渲染图谱（自动布局）
  → 用户点击节点 → emit('navigate', targetPageId)
  → 父组件切换到对应页面配置
```

---

## 后端API设计

### API接口

**路由：** `GET /page-configs/:id/relations`

**请求参数：**
- `id`: 页面配置ID（路径参数）
- `depth`: 递归深度（可选，默认3）

**响应格式：**
```json
{
  "nodes": [
    {
      "id": "page-inspection-case",
      "name": "巡检用例",
      "fields": 15
    },
    {
      "id": "page-inspection-plan",
      "name": "巡检计划",
      "fields": 8
    }
  ],
  "edges": [
    {
      "source": "page-inspection-case",
      "target": "page-inspection-plan",
      "type": "relation",
      "field": "casePlan",
      "label": "巡检计划"
    }
  ]
}
```

**错误响应：**
- `404`: 页面配置不存在
- `400`: 节点数量超过限制（>50）

---

### 递归扫描算法

**核心逻辑：**
```python
def get_page_config_relations(page_id: str, max_depth: int = 3):
    visited = set()  # 已访问页面，防止循环
    nodes = []
    edges = []

    queue = [(page_id, 0)]  # BFS队列: [(page_id, depth)]

    while queue:
        current_id, depth = queue.pop(0)

        if current_id in visited or depth > max_depth:
            continue

        visited.add(current_id)

        # 获取页面配置
        page_config = get_page_config_by_id(current_id)

        # 验证页面是否存在
        if not page_config:
            continue

        nodes.append({
            'id': current_id,
            'name': page_config['name'],
            'fields': len(page_config['fields'])
        })

        # 扫描字段关联
        for field in page_config['fields']:
            target = extract_target_collection(field)

            if target:
                edges.append({
                    'source': current_id,
                    'target': target,
                    'type': field['controlType'],
                    'field': field['fieldName'],
                    'label': field['label']
                })

                if target not in visited:
                    queue.append((target, depth + 1))

    # 验证所有目标节点存在
    validated_edges = []
    for edge in edges:
        if edge['target'] in [n['id'] for n in nodes]:
            validated_edges.append(edge)
        # 否则丢弃无效边

    return {'nodes': nodes, 'edges': validated_edges}
```

**关键点：**
1. BFS避免深度优先栈溢出
2. `visited` Set防止循环引用
3. `max_depth`控制递归层级（默认3层）
4. 验证目标页面存在，过滤无效关联

---

### 目标集合提取逻辑

```python
def extract_target_collection(field: dict) -> str | None:
    """根据字段类型提取目标集合ID"""

    if field['controlType'] == 'relation':
        config = field.get('relationConfig', {})
        return config.get('targetCollection')

    elif field['controlType'] == 'reference':
        config = field.get('referenceConfig', {})
        return config.get('targetCollection')

    elif field['controlType'] == 'quoteSelect':
        config = field.get('quoteConfig', {})
        return config.get('targetCollection')

    return None
```

---

## 前端组件设计

### 组件结构

**文件：** `src/components/PageConfigRelationGraph.vue`

**Props：**
- `pageId: string` - 当前页面配置ID

**Events：**
- `navigate(targetPageId)` - 点击节点跳转

**核心元素：**
- `<VueFlow>` - 主画布
- `<Background>` - 背景网格
- `<Controls>` - 缩放控件
- 自定义节点模板 - 显示页面名称、字段数量

---

### Vue Flow节点格式

```typescript
interface GraphNode {
  id: string          // 页面配置ID
  type: 'custom'      // 自定义节点类型
  position: { x: number, y: number }  // 自动布局会重新计算
  data: {
    name: string      // 页面名称
    fields: number    // 字段数量
    id: string        // 页面配置ID
  }
}
```

---

### Vue Flow边格式

```typescript
interface GraphEdge {
  id: string          // 唯一标识 (source-target)
  source: string      // 起始节点ID
  target: string      // 目标节点ID
  label: string       // 字段显示名称
  type: 'smoothstep'  // 边类型（平滑阶梯）
  style: {
    stroke: string    // 颜色（根据关联类型）
    strokeWidth: number  // 线宽
  }
  animated: boolean   // 动画效果（relation类型）
}
```

---

### 边样式映射

```typescript
function getEdgeStyle(type: string) {
  switch (type) {
    case 'relation':
      return { stroke: '#409EFF', strokeWidth: 2 }  // 蓝色
    case 'reference':
      return { stroke: '#67C23A', strokeWidth: 2 }  // 绿色
    case 'quoteSelect':
      return { stroke: '#E6A23C', strokeWidth: 2 }  // 橙色
    default:
      return { stroke: '#909399', strokeWidth: 2 }  // 灰色
  }
}
```

---

### 节点样式

```css
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
```

---

### 节点点击处理

```typescript
function handleNodeClick(event: { node: any }) {
  const targetPageId = event.node.id

  // 不跳转到当前页面
  if (targetPageId === props.pageId) {
    return
  }

  emit('navigate', targetPageId)
}
```

---

### 数据加载逻辑

```typescript
async function loadRelations() {
  loading.value = true

  try {
    const result = await get(`/page-configs/${props.pageId}/relations`)

    // 转换为Vue Flow格式
    nodes.value = result.nodes.map(n => ({
      id: n.id,
      type: 'custom',
      position: { x: 0, y: 0 },
      data: { name: n.name, fields: n.fields, id: n.id }
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

  } catch (error) {
    ElMessage.error('加载关系图谱失败')
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadRelations()
})

watch(() => props.pageId, () => {
  loadRelations()
})
```

---

### 集成到PageConfigManager

**新增Tab：**
```vue
<el-tab-pane label="关系图谱" name="relations">
  <PageConfigRelationGraph
    :page-id="currentPageId"
    @navigate="handleNavigateToPage"
  />
</el-tab-pane>
```

**跳转处理：**
```typescript
function handleNavigateToPage(targetPageId: string) {
  // 查找目标页面配置
  const targetConfig = pageConfigs.value.find(c => c.id === targetPageId)

  if (targetConfig) {
    handleSelect(targetConfig)  // 切换到目标页面编辑
  } else {
    ElMessage.warning('目标页面配置不存在')
  }
}
```

---

## 错误处理与边界情况

### 1. 页面配置不存在
**后端：**
```python
if not page_config_exists(page_id):
    return {'error': '页面配置不存在'}, 404
```

**前端：**
```typescript
catch (error) {
  if (error.response?.status === 404) {
    ElMessage.error('页面配置不存在')
  }
}
```

---

### 2. 空关联情况
**定义：** 页面配置无任何关联字段（relation/reference/quoteSelect）

**处理：**
- 后端返回单个节点（当前页面）
- 前端显示提示：
```vue
<el-empty
  v-if="nodes.length === 1 && edges.length === 0"
  description="当前页面配置无关联关系"
/>
```

---

### 3. 节点数量过多
**限制：**
```python
if len(nodes) > 50:
    return {
      'error': '关联节点过多（>50），建议减少递归深度',
      'hint': '请使用depth参数限制层级'
    }, 400
```

**前端处理：**
```typescript
catch (error) {
  if (error.response?.status === 400) {
    ElMessage.warning(error.response.data.error)
  }
}
```

---

### 4. 无效目标集合
**定义：** 字段配置的`targetCollection`指向不存在的页面配置

**处理：**
- 后端过滤无效边（目标节点不在nodes列表）
- 前端不显示无效关联

---

### 5. 命名不一致问题
**问题：** `targetCollection`字段值与页面配置ID不匹配

**示例：**
- 页面配置ID: `page-inspection-case`
- targetCollection: `inspection-case`

**解决方案：**
```python
# 后端规范化处理
def normalize_page_id(collection_name: str) -> str:
    """将collection名称转换为页面配置ID"""
    if collection_name.startswith('page-'):
        return collection_name
    return f'page-{collection_name}'
```

---

## 性能优化

### 1. 节点数量限制
- 后端限制最多50个节点
- 前端响应式渲染，Vue Flow自动优化

---

### 2. 懒加载
- 使用 `v-if` 在Tab切换时才渲染图谱
- 避免不必要的组件初始化

---

### 3. 缓存策略（可选）
- 监听字段配置变化，清除缓存
- 未修改时复用图谱数据

---

## 用户体验优化

### 1. 加载状态
```vue
<div v-if="loading" class="loading-state">
  <el-icon class="is-loading"><Loading /></el-icon>
  正在加载关系图谱...
</div>
```

---

### 2. 图谱交互提示
- 鼠标hover节点显示Tooltip："点击可跳转编辑"
- 边hover显示字段名称、关联类型

---

### 3. 深度提示
```vue
<div class="depth-info">
  当前展示 3 层关联关系
</div>
```

---

### 4. 导出功能（可选）
- 导出PNG图片
- 导出关系数据JSON

**实现：**
```typescript
import { useVueFlow } from '@vue-flow/core'

const { toImage } = useVueFlow()

function exportImage() {
  const dataUrl = toImage('png')
  // 下载图片
}
```

---

## 测试策略

### 后端单元测试

**测试用例：**
```python
def test_single_page_no_relations():
    """测试无关联的单个页面"""
    result = get_page_config_relations('page-simple')
    assert len(result['nodes']) == 1
    assert len(result['edges']) == 0

def test_two_page_relation():
    """测试两个页面关联"""
    result = get_page_config_relations('page-a')
    assert len(result['nodes']) == 2
    assert len(result['edges']) == 1
    assert result['edges'][0]['type'] == 'relation'

def test_circular_reference():
    """测试循环引用"""
    # A → B → A
    result = get_page_config_relations('page-a')
    assert len(result['nodes']) == 2  # 不重复
    assert len(result['edges']) == 2

def test_depth_limit():
    """测试深度限制"""
    # A → B → C → D
    result = get_page_config_relations('page-a', max_depth=2)
    assert len(result['nodes']) == 3  # A, B, C
    assert 'page-d' not in [n['id'] for n in result['nodes']]
```

---

### 前端组件测试

**测试用例：**
```typescript
describe('PageConfigRelationGraph', () => {
  it('renders nodes and edges', async () => {
    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-test' }
    })

    await flushPromises()

    expect(wrapper.vm.nodes.length).toBeGreaterThan(0)
    expect(wrapper.vm.edges.length).toBeGreaterThan(0)
  })

  it('emits navigate event on node click', async () => {
    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-a' }
    })

    await wrapper.vm.handleNodeClick({ node: { id: 'page-b' } })

    expect(wrapper.emitted('navigate')).toBeTruthy()
    expect(wrapper.emitted('navigate')[0]).toEqual(['page-b'])
  })

  it('shows empty state when no relations', async () => {
    // Mock API返回单节点
    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-isolated' }
    })

    await flushPromises()

    expect(wrapper.find('.el-empty').exists()).toBe(true)
  })
})
```

---

### 集成测试

**手动测试清单：**
- [ ] 创建页面配置A关联B，查看图谱显示两个节点
- [ ] 点击节点B，切换到B页面编辑
- [ ] 创建循环引用A→B→A，图谱不重复节点
- [ ] 修改字段配置，切换Tab重新加载图谱
- [ ] 测试深度超过3层，图谱只显示3层
- [ ] 测试节点超过50个，显示错误提示

---

## 实现清单

### Phase 1: 后端API
- [ ] 安装依赖（无）
- [ ] 创建 `get_page_config_relations()` 函数
- [ ] 新增路由 `GET /page-configs/:id/relations`
- [ ] 编写单元测试
- [ ] 测试循环引用、深度限制

### Phase 2: 前端组件
- [ ] 安装 Vue Flow依赖
- [ ] 创建 `PageConfigRelationGraph.vue`
- [ ] 实现节点/边转换逻辑
- [ ] 实现点击跳转功能
- [ ] 添加加载状态、空状态
- [ ] 编写组件测试

### Phase 3: 集成
- [ ] 集成到 `PageConfigManager.vue` 新增Tab
- [ ] 实现跳转逻辑
- [ ] 测试完整流程

### Phase 4: 优化
- [ ] 性能测试（50节点）
- [ ] 用户体验优化（Tooltip、提示）
- [ ] 文档更新

---

## 文件清单

**新增文件：**
```
server/routes/page_configs.py (修改)  # 新增路由
server/utils/page_config_relations.py (新增)  # 递归扫描函数
src/components/PageConfigRelationGraph.vue (新增)  # 图谱组件
src/api/pageConfigs.ts (修改)  # 新增API调用函数
server/tests/test_page_config_relations.py (新增)  # 后端测试
src/components/__tests__/PageConfigRelationGraph.test.ts (新增)  # 前端测试
```

**修改文件：**
```
src/views/admin/PageConfigManager.vue  # 新增Tab
```

---

## 成功标准

1. **功能完整**：
   - 能正确显示页面配置关联关系
   - 支持点击节点跳转
   - 颜色区分三种关联类型

2. **性能达标**：
   - 50节点以下加载时间<2秒
   - 交互流畅（拖拽、缩放）

3. **错误处理完善**：
   - 循环引用不崩溃
   - 无效关联不显示
   - 清晰的错误提示

4. **测试覆盖**：
   - 后端单元测试通过
   - 前端组件测试通过
   - 手动测试清单完成

---

## 未来扩展

**可选增强功能：**
- [ ] 导出PNG图片
- [ ] 导出关系JSON数据
- [ ] 节点搜索/筛选
- [ ] 关联字段详情面板
- [ ] 多个页面配置同时对比
- [ ] 全局关系图谱（独立页面）

---

## 总结

本设计通过Vue Flow实现交互式页面配置关系图谱，核心特性：

1. **BFS递归扫描** - 多层关联展示，防止循环
2. **颜色区分类型** - relation/reference/quoteSelect直观区分
3. **节点点击跳转** - 快速导航到关联页面编辑
4. **自动布局** - Vue Flow内置算法，无需手动计算
5. **性能优化** - 节点限制、懒加载、缓存

实现后可显著提升页面配置管理的可视化能力，帮助开发者理解数据结构关联关系。