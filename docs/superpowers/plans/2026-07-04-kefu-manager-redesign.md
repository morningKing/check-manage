# 客服设置页（KefuManager）重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `src/views/admin/KefuManager.vue` 从一路裸滚的单页重构为「顶部实例栏 + `el-tabs` 子标签（热门问题 / 主页配置）」，纯模板+样式、`<script>` 零改动。

**Architecture:** 只重排 `<template>` 并新增 `<style scoped>`；`<script setup>` 逻辑保持不变。`el-tabs` 非受控（首个 pane 默认）。现有 FAQ 单测补 `el-tabs`/`el-tab-pane` stub 保持绿。

**Tech Stack:** Vue 3 `<script setup>` + Element Plus（全局 `<el-*>`）+ Vitest + `@vue/test-utils`。

## Global Constraints

- **纯模板 + 样式重构**：`KefuManager.vue` 的 `<script setup>`（所有 ref/方法：`loadInstances`/`loadFaq`/`loadHome`/`onInstanceChange`/`openCreate`/`openEdit`/`save`/`remove`/`toggle`/`move`/`addBubble`/`removeBubble`/`moveBubble`/`saveHome`/`openCreateInstance`/`openEditInstance`/`removeInstance`/`onInstanceSaved`）**逐字不动**。不改后端、`api/kefu.ts`、`KefuInstanceDialog.vue`、`KefuBlocksEditor.vue`。
- `el-tabs` **不绑 `v-model`（非受控）**——首个 pane「热门问题」默认激活，避免新增 script 状态。
- 去掉 `el-page-header content="智能客服 · 热门问题"`。
- 「+ 新增热问」放入「热门问题」tab 工具条（不再与实例操作同排）。
- 颜色用 Element Plus 主题变量。
- 现有 `KefuManager.faq.test.ts` 断言基于 `vm` 方法/状态，须保持全绿（补 `el-tabs`/`el-tab-pane` stub）。前端构建 `npm run build`（vue-tsc）无错误。
- 提交只 `git add` 该任务确切文件，禁止 `git add -A`。

---

### Task 1: `KefuManager.vue` 模板 + 样式重构

**Files:**
- Modify: `src/views/admin/KefuManager.vue`（替换 `<template>`；新增 `<style scoped>`；`<script>` 不动）
- Modify: `src/views/admin/__tests__/KefuManager.faq.test.ts`（stubs 补 `el-tabs`/`el-tab-pane`）

**Interfaces:**
- Consumes: 现有 script 暴露的 ref/方法（不变）；`KefuBlocksEditor`、`KefuInstanceDialog`、`MdEditor`（不变）。

- [ ] **Step 1: 替换 `<template>` 全段**

把 `src/views/admin/KefuManager.vue` 的 `<template>…</template>`（第 1–71 行，到 `</template>` 为止）整体替换为：

```html
<template>
  <div class="kefu-manager">
    <!-- 顶部实例栏 -->
    <div class="km-topbar">
      <span class="km-topbar__label">客服实例</span>
      <el-select v-model="activeIid" placeholder="选择客服实例" @change="onInstanceChange" style="width:260px">
        <el-option v-for="i in instances" :key="i.id" :label="i.name" :value="i.id" />
      </el-select>
      <el-button type="primary" @click="openCreateInstance">+ 新建客服</el-button>
      <el-button v-if="activeIid" @click="openEditInstance">编辑客服</el-button>
      <el-button v-if="activeIid" type="danger" @click="removeInstance">删除客服</el-button>
    </div>

    <!-- 空状态 -->
    <div v-if="!activeIid" class="km-empty">
      {{ instances.length ? '请选择上方的客服实例开始配置' : '还没有客服实例，点「+ 新建客服」创建一个' }}
    </div>

    <!-- 子标签：热门问题 / 主页配置 -->
    <el-tabs v-else class="km-tabs">
      <el-tab-pane label="热门问题">
        <div class="km-tab-body">
          <div class="km-toolbar">
            <el-button type="primary" @click="openCreate">+ 新增热问</el-button>
          </div>
          <el-table :data="faqs" row-key="id">
            <el-table-column label="排序" width="70">
              <template #default="{ $index }">
                <el-button link :disabled="$index===0" @click="move($index,-1)">↑</el-button>
                <el-button link :disabled="$index===faqs.length-1" @click="move($index,1)">↓</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="question" label="问题" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="click_count" label="点击量" width="90" />
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" @change="(v: boolean) => toggle(row, v)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button link type="danger" @click="remove(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="主页配置">
        <div class="km-tab-body">
          <div class="home-config-section">
            <div class="section-title">提示气泡（guided_questions）</div>
            <div v-for="(_, idx) in bubbles" :key="idx" class="bubble-row">
              <el-input v-model="bubbles[idx]" placeholder="提示语" style="width:360px" />
              <el-button link :disabled="idx===0" @click="moveBubble(idx,-1)">↑</el-button>
              <el-button link :disabled="idx===bubbles.length-1" @click="moveBubble(idx,1)">↓</el-button>
              <el-button link type="danger" @click="removeBubble(idx)">删除</el-button>
            </div>
            <el-button size="small" style="margin-top:6px" @click="addBubble">+ 添加气泡</el-button>
          </div>

          <div class="home-config-section" style="margin-top:16px">
            <KefuBlocksEditor v-model="blocks" />
          </div>

          <el-button type="primary" style="margin-top:16px" @click="saveHome">保存主页配置</el-button>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- FAQ 编辑对话框 -->
    <el-dialog v-model="dialog" :title="editing?.id ? '编辑热问' : '新增热问'" width="720px">
      <el-form label-width="72px">
        <el-form-item label="问题"><el-input v-model="form.question" /></el-form-item>
        <el-form-item label="分类"><el-input v-model="form.category" placeholder="可选标签" /></el-form-item>
        <el-form-item label="答案">
          <MdEditor v-model="form.answer" style="height:320px" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog=false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <KefuInstanceDialog v-model="showInstanceDialog" :instance="editingInstance" @saved="onInstanceSaved" />
  </div>
</template>
```

（注意：`<script setup>` 段与其后所有内容保持不变——只替换 `<template>`。）

- [ ] **Step 2: 新增 `<style scoped>`**

在 `</script>` 之后（文件末尾）追加：

```html
<style scoped>
.kefu-manager { padding: 4px 0; }
.km-topbar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 14px; margin-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.km-topbar__label { font-weight: 600; color: var(--el-text-color-regular, #606266); }
.km-empty {
  padding: 48px 0; text-align: center;
  color: var(--el-text-color-secondary, #909399);
}
.km-tabs { margin-top: 4px; }
.km-tab-body { padding: 8px 2px 4px; }
.km-toolbar { margin-bottom: 12px; }
.section-title { font-weight: 600; color: var(--el-text-color-regular, #606266); margin-bottom: 8px; }
.bubble-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
</style>
```

- [ ] **Step 3: 现有 FAQ 测试补 `el-tabs`/`el-tab-pane` stub**

在 `src/views/admin/__tests__/KefuManager.faq.test.ts` 的 `stubs` 对象里追加两项（与其它 `el-*` stub 并列）：

```typescript
  'el-tabs': { template: '<div><slot /></div>' },
  'el-tab-pane': { template: '<div><slot /></div>', props: ['label'] },
```

- [ ] **Step 4: 运行 FAQ 测试 + 构建**

Run: `npx vitest run src/views/admin/__tests__/KefuManager.faq.test.ts`
Expected: PASS（6 用例；断言基于 `vm` 方法/状态，模板重排 + 新 stub 不影响）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/KefuManager.vue src/views/admin/__tests__/KefuManager.faq.test.ts
git commit -m "feat(kefu): 客服设置页重构为实例栏 + 子标签（热门问题/主页配置）"
```

---

### Task 2: 文档 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（管理页结构描述）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 确认前端在运行**

前端 `http://localhost:5173`（Vite HMR 自动生效重构）；后端 `http://localhost:3002`。若未运行则启动 `npm run dev:all`。

- [ ] **Step 2: Playwright 走结构验证**

Playwright（admin/admin123 登录），访问 `http://localhost:5173/admin/integration?tab=kefu`：
1. 断言顶部实例栏：`客服实例` 下拉 + 「+ 新建客服」；未选实例时显示空状态文案「请选择上方的客服实例开始配置」（demo 存在时）。
2. 从下拉选中「演示客服」→ 出现「编辑客服/删除客服」按钮 + `el-tabs`，默认「热门问题」tab：显示「+ 新增热问」+ FAQ 表格（含 demo 的热问行）。
3. 点「主页配置」tab → 显示提示气泡编辑 + 区块编辑器 + 「保存主页配置」按钮。
4. 抽查一次操作生效：在「主页配置」点「保存主页配置」→ 断言出现「主页配置已保存」成功提示。
5. 截图存 `.playwright-mcp/kefu-manager-redesign.png`（默认热门问题 tab）。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` §9.2「访问管理页面」附近，补一句管理页结构：「顶部为客服实例栏（选择/新建/编辑/删除），下方为『热门问题 / 主页配置』两个子标签」；若该处有旧页面截图则替换为新结构截图。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 客服设置页新结构（实例栏 + 子标签）说明"
```

---

## Self-Review

**Spec coverage:**
- 顶部实例栏（选择/新建/编辑/删除）+ 空状态 → Task 1 Step 1 ✓
- `el-tabs` 子标签（热门问题/主页配置）、非受控默认 → Task 1 Step 1 ✓
- 「新增热问」移入 FAQ tab 工具条 → Task 1 Step 1 ✓
- 去掉 `el-page-header` → Task 1 Step 1（新模板无该标签）✓
- `<script>` 零改动 → Task 1 只替换 `<template>` + 追加 `<style>`，未触碰 `<script setup>` ✓
- 复用 `section-title`/`bubble-row` + 主题变量样式 → Task 1 Step 2 ✓
- 现有 FAQ 测试保持绿（补 stub）→ Task 1 Step 3 ✓
- 文档 + Playwright → Task 2 ✓

**Placeholder scan:** 无 TBD/TODO；模板/样式为完整代码；测试步骤有确定命令与预期。✓

**Type consistency:**
- 新模板绑定的方法/ref 名（`activeIid`/`instances`/`onInstanceChange`/`openCreateInstance`/`openEditInstance`/`removeInstance`/`openCreate`/`faqs`/`move`/`toggle`/`openEdit`/`remove`/`bubbles`/`moveBubble`/`removeBubble`/`addBubble`/`blocks`/`saveHome`/`dialog`/`editing`/`form`/`save`/`showInstanceDialog`/`editingInstance`/`onInstanceSaved`）全部来自现有 `<script setup>`，逐一对应、未新增未改名 ✓
- 新增 class（`km-topbar`/`km-empty`/`km-tabs`/`km-tab-body`/`km-toolbar`）在模板与样式间一致；`section-title`/`bubble-row` 沿用 ✓
