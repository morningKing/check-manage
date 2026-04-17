# 版本标识显示实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据页面和编辑/查看对话框中显示当前分支信息，让用户清楚数据来源。

**Architecture:** 在DynamicPage.vue组件中添加版本状态获取和显示逻辑，页面标题旁显示分支标签+切换下拉按钮，对话框标题右侧同步显示分支标签。

**Tech Stack:** Vue 3 + TypeScript + Element Plus

---

## 文件结构

| 文件 | 修改内容 |
|------|----------|
| `src/views/dynamic/DynamicPage.vue` | 添加分支状态、标签显示、切换下拉菜单 |
| `src/api/version.ts` | 无需修改，使用现有API |
| `src/types/version.ts` | 无需修改，使用现有类型 |

---

## Task 1: 添加分支状态和导入

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue:700-710` (imports)
- Modify: `src/views/dynamic/DynamicPage.vue:747-800` (state section)

- [ ] **Step 1: 添加API导入**

在导入区域（约第703行附近）添加版本API导入：

```typescript
import { getCurrentBranch, getVersions, switchToVersion, switchToMainBranch, type UserBranch } from '@/api/version'
```

- [ ] **Step 2: 添加分支状态**

在state区域（约第935行 `versionManagerVisible` 定义附近）添加：

```typescript
/**
 * 当前用户分支信息
 */
const currentBranch = ref<UserBranch | null>(null)

/**
 * 分支列表（用于切换下拉菜单）
 */
const branchVersions = ref<CollectionVersion[]>([])

/**
 * 分支切换下拉菜单可见性
 */
const showBranchDropdown = ref(false)

/**
 * 分支切换加载状态
 */
const branchSwitching = ref(false)
```

需要导入 `CollectionVersion` 类型，在现有的 types 导入中添加：

```typescript
import type { PageConfig, FieldConfig, DynamicRecord, ExportScript, KanbanConfig, FieldOption, DeleteBindingConfig, CollectionVersion } from '@/types'
```

- [ ] **Step 3: 运行类型检查验证**

Run: `cd E:/wsl/check/check-manage && npm run build`
Expected: 类型检查通过，无新增错误

- [ ] **Step 4: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): add branch state variables in DynamicPage

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 添加加载当前分支函数

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue` (在loadPageData后添加函数)

- [ ] **Step 1: 添加loadCurrentBranch函数**

在 `loadPageData` 函数（约第1330行）后添加：

```typescript
/**
 * 加载当前用户分支信息
 */
async function loadCurrentBranch(): Promise<void> {
  if (!collection.value) return
  
  try {
    currentBranch.value = await getCurrentBranch(collection.value)
  } catch (error) {
    console.error('获取分支信息失败:', error)
    currentBranch.value = null
  }
}

/**
 * 加载分支列表（用于切换下拉菜单）
 */
async function loadBranchVersions(): Promise<void> {
  if (!collection.value) return
  
  try {
    const versions = await getVersions(collection.value)
    // 只筛选分支类型
    branchVersions.value = versions.filter(v => v.versionType === 'branch')
  } catch (error) {
    console.error('获取分支列表失败:', error)
    branchVersions.value = []
  }
}
```

- [ ] **Step 2: 在loadPageData中调用loadCurrentBranch**

修改 `loadPageData` 函数，在 try 块末尾（约第1322行 `totalCount.value = result.total` 后）添加：

```typescript
// 加载当前分支信息
await loadCurrentBranch()
```

- [ ] **Step 3: 运行类型检查**

Run: `npm run build`
Expected: 类型检查通过

- [ ] **Step 4: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): add loadCurrentBranch and loadBranchVersions functions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: 修改页面标题区域添加分支标签

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue:18-24` (page-header区域)

- [ ] **Step 1: 修改page-title区域**

将原有的 page-title 区域（第19-23行）替换为：

```vue
<div class="page-title">
  <h2>{{ pageConfig?.name || '数据页面' }}</h2>
  <!-- 分支标签 -->
  <el-tag
    v-if="currentBranch"
    :type="currentBranch.branchId ? 'primary' : 'success'"
    size="small"
    style="margin-left: 12px"
  >
    {{ currentBranch.branchName }}
  </el-tag>
  <!-- 切换下拉按钮（仅管理员可见） -->
  <el-dropdown
    v-if="isAdmin && currentBranch"
    trigger="click"
    @command="handleBranchSwitch"
    style="margin-left: 8px"
  >
    <span class="branch-switch-link">
      切换 <el-icon class="el-icon--right"><ArrowDown /></el-icon>
    </span>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          :command="'main'"
          :disabled="!currentBranch.branchId"
        >
          <el-icon v-if="!currentBranch.branchId"><Check /></el-icon>
          主分支
        </el-dropdown-item>
        <el-dropdown-item
          v-for="branch in branchVersions"
          :key="branch.id"
          :command="branch.id"
          :disabled="branch.id === currentBranch.branchId"
        >
          <el-icon v-if="branch.id === currentBranch.branchId"><Check /></el-icon>
          {{ branch.name }}
        </el-dropdown-item>
        <el-dropdown-item divided command="manage">
          <el-icon><Tickets /></el-icon>
          管理版本...
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
  <span v-if="pageConfig?.description" class="page-description">
    {{ pageConfig.description }}
  </span>
</div>
```

需要添加 `Check` 图标导入，在图标导入行（约第702行）添加：

```typescript
import { Plus, Refresh, Upload, Download, ArrowDown, Search, Delete, DCaret, Grid, Operation, MagicStick, Tickets, Document, Loading, Back, Check } from '@element-plus/icons-vue'
```

- [ ] **Step 2: 添加handleBranchSwitch处理函数**

在函数区域添加：

```typescript
/**
 * 处理分支切换
 */
async function handleBranchSwitch(command: string): void {
  if (command === 'manage') {
    versionManagerVisible.value = true
    return
  }
  
  if (command === 'main') {
    // 切换到主分支
    branchSwitching.value = true
    try {
      await switchToMainBranch(collection.value)
      await loadCurrentBranch()
      await loadPageData()
      ElMessage.success('已切换到主分支')
    } catch (error: any) {
      const msg = error?.response?.data?.error || '切换失败'
      ElMessage.error(msg)
    } finally {
      branchSwitching.value = false
    }
    return
  }
  
  // 切换到指定分支
  branchSwitching.value = true
  try {
    const result = await switchToVersion(command)
    await loadCurrentBranch()
    await loadPageData()
    
    let msg = `已切换到分支「${result.branchName}」`
    if (result.initialized) {
      msg += '（分支数据已初始化）'
    }
    ElMessage.success(msg)
    
    // 如果影响多个Collection，通知其他页面刷新
    if (result.affectedCollections && result.affectedCollections.length > 1) {
      branchRefreshStore.requestRefresh(result.affectedCollections)
    }
  } catch (error: any) {
    const msg = error?.response?.data?.error || '切换失败'
    ElMessage.error(msg)
  } finally {
    branchSwitching.value = false
  }
}
```

- [ ] **Step 3: 添加CSS样式**

在 `<style>` 区域添加：

```scss
.branch-switch-link {
  color: #409eff;
  font-size: 14px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  
  &:hover {
    color: #66b1ff;
  }
}
```

- [ ] **Step 4: 运行类型检查**

Run: `npm run build`
Expected: 类型检查通过

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): add branch tag and switch dropdown in page header

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: 编辑对话框添加分支标签

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue:280-307` (编辑对话框)

- [ ] **Step 1: 修改编辑对话框标题**

将编辑对话框（约第280-307行）的标题部分改为使用自定义header：

```vue
<!-- 新增/编辑对话框 -->
<el-dialog
  v-model="dialogVisible"
  width="600px"
  :close-on-click-modal="false"
  destroy-on-close
>
  <template #header>
    <div style="display: flex; align-items: center;">
      <span style="font-weight: bold;">{{ dialogTitle }}</span>
      <el-tag
        v-if="currentBranch"
        :type="currentBranch.branchId ? 'primary' : 'success'"
        size="small"
        style="margin-left: auto;"
      >
        {{ currentBranch.branchName }}
      </el-tag>
    </div>
  </template>
  <DynamicForm
    ref="dynamicFormRef"
    :fields="pageFields"
    :initial-data="currentRecord"
    :show-actions="false"
    @submit="handleSubmit"
  />
  <template #footer>
    <el-button @click="dialogVisible = false" :disabled="submitLoading">
      取消
    </el-button>
    <el-button
      type="primary"
      @click="handleFormSubmit"
      :loading="submitLoading"
    >
      确定
    </el-button>
  </template>
</el-dialog>
```

- [ ] **Step 2: 运行类型检查**

Run: `npm run build`
Expected: 类型检查通过

- [ ] **Step 3: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): add branch tag in edit dialog header

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 查看对话框添加分支标签

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue:309-315` (查看对话框)

- [ ] **Step 1: 修改查看对话框标题**

将查看对话框（约第309-315行）的标题部分改为：

```vue
<!-- 查看记录对话框 -->
<el-dialog
  v-model="viewDialogVisible"
  width="700px"
  destroy-on-close
>
  <template #header>
    <div style="display: flex; align-items: center;">
      <span style="font-weight: bold;">查看记录</span>
      <el-tag
        v-if="currentBranch"
        :type="currentBranch.branchId ? 'primary' : 'success'"
        size="small"
        style="margin-left: auto;"
      >
        {{ currentBranch.branchName }}
      </el-tag>
    </div>
  </template>
  <el-descriptions :column="1" border>
    <!-- ... 保持原有内容不变 ... -->
  </el-descriptions>
</el-dialog>
```

- [ ] **Step 2: 运行类型检查**

Run: `npm run build`
Expected: 类型检查通过

- [ ] **Step 3: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): add branch tag in view dialog header

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: 监听分支刷新事件

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue` (watch区域)

- [ ] **Step 1: 添加分支刷新监听**

在现有的watch区域（约第1300行附近）添加对branchRefreshStore的监听：

```typescript
// 监听跨Collection分支切换刷新请求
watch(
  () => branchRefreshStore.refreshTimestamp,
  () => {
    if (branchRefreshStore.needsRefresh(collection.value)) {
      loadPageData()
      loadCurrentBranch()
      branchRefreshStore.clearRefresh()
    }
  }
)
```

- [ ] **Step 2: 运行类型检查**

Run: `npm run build`
Expected: 类型检查通过

- [ ] **Step 3: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): watch branchRefreshStore for cross-collection refresh

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: 运行完整测试

**Files:**
- Test: 所有单元测试

- [ ] **Step 1: 运行前端测试**

Run: `npm run test -- --run`
Expected: 435 passed

- [ ] **Step 2: 运行后端测试**

Run: `cd server && python -m pytest tests/ -v`
Expected: 453 passed, 3 skipped

- [ ] **Step 3: 运行完整构建**

Run: `npm run build`
Expected: Build successful

- [ ] **Step 4: Push所有提交**

```bash
git push origin main
```

---

## 验收清单

完成后验证：

1. ✅ 数据主页标题旁显示当前分支标签
2. ✅ 主分支显示绿色标签「主分支」
3. ✅ 其他分支显示蓝色标签（分支名称）
4. ✅ 点击「切换 ▼」弹出分支列表下拉菜单
5. ✅ 下拉菜单底部有「管理版本...」入口
6. ✅ 选择分支后成功切换并刷新数据
7. ✅ 编辑对话框标题右侧显示分支标签
8. ✅ 查看对话框标题右侧显示分支标签
9. ✅ 所有测试通过
10. ✅ 构建成功