# 引用/关联字段导出显示值回退 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `quoteSelect`/`relation` 字段在目标集合无主键时，导出 `displayField`（界面真实值）而不是内部 id。

**Architecture:** 在 `fetchRelationDisplayMaps` 与 `fetchQuoteDisplayMaps` 里把"无主键就跳过"改为 `labelField = pkField || config.displayField` 回退；有主键时行为不变。纯前端 store 改动。

**Tech Stack:** Vue 3 + TypeScript + Pinia，Vitest。

---

## File Structure

- `src/stores/pageConfig.ts`（改）— `fetchRelationDisplayMaps`、`fetchQuoteDisplayMaps` 各一处。
- `src/stores/__tests__/pageConfig.test.ts`（改）— 新增 3 个用例。

---

## Task 1: 无主键时回退 displayField + 测试

**Files:**
- Modify: `src/stores/pageConfig.ts` (`fetchRelationDisplayMaps` ~`:1054`, `fetchQuoteDisplayMaps` ~`:1203`)
- Test: `src/stores/__tests__/pageConfig.test.ts`

- [ ] **Step 1: Write the failing tests** — append these three tests inside the existing top-level `describe(...)` block in `src/stores/__tests__/pageConfig.test.ts` (right after the existing `it('fetchQuoteDisplayMaps 构建正确映射', ...)` test). They reuse the file's existing `makePageConfig` / `makeField` helpers and the `get` mock; `store` is the active store from the suite's `beforeEach`:

```ts
  it('fetchQuoteDisplayMaps 目标无主键时回退 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'quotedCases', controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            // 目标集合无主键字段
            makeField({ id: 'nm', fieldName: 'caseName', controlType: 'text' }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '苹果' },
        { id: 'case-2', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchQuoteDisplayMaps('page-test')
    expect(maps.quotedCases).toBeDefined()
    expect(maps.quotedCases.get('case-1')).toBe('苹果')   // displayField, 不是 id
    expect(maps.quotedCases.get('case-2')).toBe('香蕉')
  })

  it('fetchRelationDisplayMaps 目标无主键时回退 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'relCases', controlType: 'relation',
              relationConfig: { targetCollection: 'cases', targetField: 'back', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'nm', fieldName: 'caseName', controlType: 'text' }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '苹果' },
        { id: 'case-2', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchRelationDisplayMaps('page-test')
    expect(maps.relCases).toBeDefined()
    expect(maps.relCases.get('case-1')).toBe('苹果')
    expect(maps.relCases.get('case-2')).toBe('香蕉')
  })

  it('fetchRelationDisplayMaps 目标有主键时仍用主键值', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'relCases', controlType: 'relation',
              relationConfig: { targetCollection: 'cases', targetField: 'back', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001', caseName: '苹果' },
        { id: 'case-2', caseId: 'IC-002', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchRelationDisplayMaps('page-test')
    expect(maps.relCases.get('case-1')).toBe('IC-001')   // 有主键 → 主键值，行为不变
    expect(maps.relCases.get('case-2')).toBe('IC-002')
  })
```

> 注：若 `makeField`/`makePageConfig` 对 `relationConfig` 的可选字段要求不同（如 `targetField` 是否必填），按该文件既有 relation 测试的写法对齐；断言保持不变。

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts -t "回退 displayField"`
Expected: the two "回退 displayField" tests FAIL — `maps.quotedCases` / `maps.relCases` is `undefined` (current code `continue`s when no pkField). (The "有主键时仍用主键值" test passes already.)

- [ ] **Step 3: Implement — fetchRelationDisplayMaps**

In `src/stores/pageConfig.ts`, in `fetchRelationDisplayMaps`, replace:
```ts
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      if (!pkField) continue
      try {
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
        const idToPk = new Map<string, string>()
        for (const r of records) {
          const pkVal = r[pkField]
          if (pkVal) idToPk.set(r.id, String(pkVal))
        }
        result[field.fieldName] = idToPk
      } catch {
```
with:
```ts
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      // 无主键时回退到 displayField（界面真实值）；导入侧按主键值/displayField 双匹配，故仍可重新导入
      const labelField = pkField || config.displayField
      if (!labelField) continue
      try {
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
        const idToLabel = new Map<string, string>()
        for (const r of records) {
          const val = r[labelField]
          if (val) idToLabel.set(r.id, String(val))
        }
        result[field.fieldName] = idToLabel
      } catch {
```

- [ ] **Step 4: Implement — fetchQuoteDisplayMaps**

In the same file, in `fetchQuoteDisplayMaps`, replace:
```ts
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      if (!pkField) continue
      try {
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
        const idToPk = new Map<string, string>()
        for (const r of records) {
          const pkVal = r[pkField]
          if (pkVal) idToPk.set(r.id, String(pkVal))
        }
        result[field.fieldName] = idToPk
      } catch {
```
with:
```ts
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      // 无主键时回退到 displayField（界面真实值）；导入侧按主键值/displayField 双匹配，故仍可重新导入
      const labelField = pkField || config.displayField
      if (!labelField) continue
      try {
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
        const idToLabel = new Map<string, string>()
        for (const r of records) {
          const val = r[labelField]
          if (val) idToLabel.set(r.id, String(val))
        }
        result[field.fieldName] = idToLabel
      } catch {
```

(Both `idToPk` references inside each block become `idToLabel`. Leave the `catch` body and the rest of each function unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts`
Expected: PASS — all existing pageConfig tests (incl. `fetchQuoteDisplayMaps 构建正确映射` with pk) plus the 3 new tests.

- [ ] **Step 6: Type-check**

Run: `npx vue-tsc --noEmit 2>&1 | grep -i "pageConfig" | head`
Expected: no NEW errors referencing the changed functions.

- [ ] **Step 7: Commit**

```bash
git add src/stores/pageConfig.ts src/stores/__tests__/pageConfig.test.ts
git commit -m "fix(export): fall back to displayField when relation/quote target has no primary key"
```

---

## Self-Review 结果

- **Spec 覆盖：** 两个函数的 `labelField = pkField || config.displayField` 回退（Step 3/4）；有主键不变（Step 4 保留 pk 优先 + Step 1 第三个测试断言）；两者都没有时 `continue`（保留）；测试三个用例（Step 1）。`reference` 不在范围、后端不动 —— 与 spec 一致。
- **Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整 before/after。Step 1 的 fixture 注解是说明性，断言明确。
- **类型/命名一致：** 两处均把 `idToPk`→`idToLabel`、`pkVal`→`val`，`labelField` 命名一致；`config.displayField` 在 relationConfig/quoteConfig 均存在（resolve 函数已使用）；测试里 `fetchQuoteDisplayMaps`/`fetchRelationDisplayMaps` 与 store 导出名一致。
