/**
 * DynamicPage 查看功能 — 辅助函数单元测试
 *
 * 测试 viewDisplayFields 的字段展开逻辑和日期格式化。
 */
import { describe, it, expect } from 'vitest'
import type { FieldConfig } from '@/types'

/**
 * 复刻 DynamicPage.viewDisplayFields 的逻辑，用于单元测试
 */
function buildViewDisplayFields(
  pageFields: FieldConfig[],
  getTargetFields: (collection: string) => FieldConfig[]
): FieldConfig[] {
  const result: FieldConfig[] = []
  for (const field of pageFields) {
    if (field.hidden) continue
    result.push(field)
    if (field.controlType === 'reference' && field.referenceConfig?.inheritFields?.length) {
      const config = field.referenceConfig
      const targetFields = getTargetFields(config.targetCollection)
      for (const inheritFieldName of config.inheritFields) {
        const parentField = targetFields.find((f) => f.fieldName === inheritFieldName)
        result.push({
          id: `_ref_${field.fieldName}_${inheritFieldName}`,
          fieldName: `_ref_${field.fieldName}_${inheritFieldName}`,
          label: parentField?.label || inheritFieldName,
          controlType: parentField?.controlType || 'text',
          required: false,
          order: field.order + 0.1,
          hidden: false,
          disabled: true,
          options: parentField?.options
        })
      }
    }
  }
  return result.sort((a, b) => a.order - b.order)
}

/**
 * 复刻 DynamicPage.formatViewDate 的逻辑
 */
function formatViewDate(value: any, controlType: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return String(value)
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    if (controlType === 'date') return `${y}-${m}-${d}`
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
  } catch {
    return String(value)
  }
}

/**
 * 复刻 DynamicPage.formatViewValue 的逻辑
 */
function formatViewValue(
  field: FieldConfig,
  record: Record<string, any>
): string {
  const value = record[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}

function makeField(overrides: Partial<FieldConfig> = {}): FieldConfig {
  return {
    id: 'f1',
    fieldName: 'name',
    label: '名称',
    controlType: 'text',
    order: 0,
    required: false,
    hidden: false,
    ...overrides,
  } as FieldConfig
}

describe('DynamicPage — 查看功能', () => {
  describe('viewDisplayFields', () => {
    it('普通字段直接包含在结果中', () => {
      const fields = [
        makeField({ id: 'f1', fieldName: 'name', label: '名称', order: 1 }),
        makeField({ id: 'f2', fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('name')
      expect(result[1].fieldName).toBe('age')
    })

    it('隐藏字段不包含在结果中', () => {
      const fields = [
        makeField({ id: 'f1', fieldName: 'name', order: 1, hidden: false }),
        makeField({ id: 'f2', fieldName: 'secret', order: 2, hidden: true }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result).toHaveLength(1)
      expect(result[0].fieldName).toBe('name')
    })

    it('reference 字段展开继承字段', () => {
      const fields = [
        makeField({
          id: 'f1',
          fieldName: 'parentRef',
          label: '父记录',
          controlType: 'reference',
          order: 1,
          referenceConfig: {
            targetCollection: 'parents',
            displayField: 'parentName',
            inheritFields: ['status', 'level'],
          }
        }),
      ]
      const targetFields = [
        makeField({ fieldName: 'status', label: '状态', controlType: 'select', options: [{ label: '启用', value: 'active' }] }),
        makeField({ fieldName: 'level', label: '级别', controlType: 'text' }),
      ]
      const result = buildViewDisplayFields(fields, () => targetFields)
      expect(result).toHaveLength(3)
      expect(result[0].fieldName).toBe('parentRef')
      expect(result[1].fieldName).toBe('_ref_parentRef_status')
      expect(result[1].label).toBe('状态')
      expect(result[2].fieldName).toBe('_ref_parentRef_level')
    })

    it('结果按 order 排序', () => {
      const fields = [
        makeField({ id: 'f2', fieldName: 'b', order: 3 }),
        makeField({ id: 'f1', fieldName: 'a', order: 1 }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result[0].fieldName).toBe('a')
      expect(result[1].fieldName).toBe('b')
    })
  })

  describe('formatViewDate', () => {
    it('date 类型只显示日期', () => {
      expect(formatViewDate('2025-06-15T08:30:00Z', 'date')).toBe('2025-06-15')
    })

    it('datetime 类型显示日期和时间', () => {
      const result = formatViewDate('2025-06-15T08:30:00Z', 'datetime')
      expect(result).toContain('2025-06-15')
      expect(result).toContain(':')
    })

    it('空值返回 -', () => {
      expect(formatViewDate(null, 'date')).toBe('-')
      expect(formatViewDate('', 'date')).toBe('-')
      expect(formatViewDate(undefined, 'date')).toBe('-')
    })

    it('无效日期返回原始字符串', () => {
      expect(formatViewDate('not-a-date', 'date')).toBe('not-a-date')
    })
  })

  describe('formatViewValue', () => {
    it('选项字段返回标签', () => {
      const field = makeField({
        fieldName: 'status',
        controlType: 'select',
        options: [
          { label: '启用', value: 'active' },
          { label: '禁用', value: 'inactive' },
        ]
      })
      expect(formatViewValue(field, { status: 'active' })).toBe('启用')
    })

    it('未匹配选项返回原始值', () => {
      const field = makeField({
        fieldName: 'status',
        controlType: 'select',
        options: [{ label: '启用', value: 'active' }]
      })
      expect(formatViewValue(field, { status: 'unknown' })).toBe('unknown')
    })

    it('空值返回 -', () => {
      const field = makeField({ fieldName: 'status', controlType: 'select' })
      expect(formatViewValue(field, { status: null })).toBe('-')
      expect(formatViewValue(field, { status: '' })).toBe('-')
      expect(formatViewValue(field, {})).toBe('-')
    })
  })

  describe('highlightRecord 分页定位', () => {
    /**
     * 复刻 highlightRecord 中计算目标分页的逻辑
     *
     * @param recordId - 目标记录 ID
     * @param filteredData - 当前过滤后的全量数据
     * @param currentPageSize - 每页条数
     * @returns 目标记录所在的页码（1-based），-1 表示未找到
     */
    function calcTargetPage(
      recordId: string,
      filteredData: { id: string }[],
      currentPageSize: number
    ): number {
      const index = filteredData.findIndex(r => r.id === recordId)
      if (index === -1) return -1
      return Math.floor(index / currentPageSize) + 1
    }

    it('第一条记录在第 1 页', () => {
      const data = Array.from({ length: 100 }, (_, i) => ({ id: `r${i}` }))
      expect(calcTargetPage('r0', data, 50)).toBe(1)
    })

    it('第 50 条记录（索引 49）在第 1 页', () => {
      const data = Array.from({ length: 100 }, (_, i) => ({ id: `r${i}` }))
      expect(calcTargetPage('r49', data, 50)).toBe(1)
    })

    it('第 51 条记录（索引 50）在第 2 页', () => {
      const data = Array.from({ length: 100 }, (_, i) => ({ id: `r${i}` }))
      expect(calcTargetPage('r50', data, 50)).toBe(2)
    })

    it('最后一条记录在正确的页', () => {
      const data = Array.from({ length: 120 }, (_, i) => ({ id: `r${i}` }))
      // 120 条，每页 50 → 第 3 页（索引 100-119）
      expect(calcTargetPage('r119', data, 50)).toBe(3)
    })

    it('每页 20 条时的分页计算', () => {
      const data = Array.from({ length: 55 }, (_, i) => ({ id: `r${i}` }))
      expect(calcTargetPage('r0', data, 20)).toBe(1)
      expect(calcTargetPage('r19', data, 20)).toBe(1)
      expect(calcTargetPage('r20', data, 20)).toBe(2)
      expect(calcTargetPage('r39', data, 20)).toBe(2)
      expect(calcTargetPage('r40', data, 20)).toBe(3)
      expect(calcTargetPage('r54', data, 20)).toBe(3)
    })

    it('记录不存在时返回 -1', () => {
      const data = [{ id: 'r1' }, { id: 'r2' }]
      expect(calcTargetPage('r999', data, 50)).toBe(-1)
    })

    it('空数据返回 -1', () => {
      expect(calcTargetPage('r1', [], 50)).toBe(-1)
    })
  })

  /**
   * 复刻 DynamicPage 中判断是否需要切换分支的逻辑
   */
  function shouldSwitchBranch(intent: { branchId?: string } | null): boolean {
    // branchId 必须存在且不为空字符串、不为 'main'
    const branchId = intent?.branchId
    return branchId != null && branchId !== '' && branchId !== 'main'
  }

  /**
   * DynamicPage 页面加载逻辑单元测试
   */
  describe('DynamicPage — 分支自动切换', () => {
    describe('shouldSwitchBranch', () => {
      it('branchId 为 main 时不应切换', () => {
        expect(shouldSwitchBranch({ branchId: 'main' })).toBe(false)
      })

      it('branchId 为其他值时应切换', () => {
        expect(shouldSwitchBranch({ branchId: 'branch-001' })).toBe(true)
      })

      it('branchId 未设置时应不切换', () => {
        expect(shouldSwitchBranch({ branchId: undefined })).toBe(false)
        expect(shouldSwitchBranch({})).toBe(false)
      })

      it('intent 为 null 时应不切换', () => {
        expect(shouldSwitchBranch(null)).toBe(false)
      })

      it('branchId 为空字符串时应不切换', () => {
        expect(shouldSwitchBranch({ branchId: '' })).toBe(false)
      })
    })

    describe('跳转携带分支上下文场景', () => {
      /**
       * 模拟跳转意图处理流程
       */
      function processJumpIntent(
        intent: { branchId?: string; targetRecordId?: string } | null,
        currentBranchId: string | null,
        switchBranch: (_id: string) => { success: boolean; branchName: string } | null
      ): { switched: boolean; branchName?: string; locateId?: string } {
        const result: { switched: boolean; branchName?: string; locateId?: string } = {
          switched: false
        }

        if (!intent) return result

        // 检查是否需要切换分支
        if (shouldSwitchBranch(intent) && intent.branchId !== currentBranchId) {
          const switchResult = switchBranch(intent.branchId!)
          if (switchResult?.success) {
            result.switched = true
            result.branchName = switchResult.branchName
          }
        }

        // 设置定位ID
        if (intent.targetRecordId) {
          result.locateId = intent.targetRecordId
        }

        return result
      }

      it('从主分支跳转到其他分支时应切换', () => {
        const intent = { branchId: 'branch-001', targetRecordId: 'record-123' }
        const result = processJumpIntent(intent, null, (_id) => ({
          success: true,
          branchName: '测试分支'
        }))
        expect(result.switched).toBe(true)
        expect(result.branchName).toBe('测试分支')
        expect(result.locateId).toBe('record-123')
      })

      it('已在目标分支时不应再次切换', () => {
        const intent = { branchId: 'branch-001', targetRecordId: 'record-123' }
        const result = processJumpIntent(intent, 'branch-001', (_id) => ({
          success: true,
          branchName: '测试分支'
        }))
        expect(result.switched).toBe(false)
        expect(result.locateId).toBe('record-123')
      })

      it('跳转到 main 分支时不切换', () => {
        const intent = { branchId: 'main', targetRecordId: 'record-123' }
        const result = processJumpIntent(intent, 'branch-001', (_id) => ({
          success: true,
          branchName: '主分支'
        }))
        expect(result.switched).toBe(false)
        expect(result.locateId).toBe('record-123')
      })

      it('切换失败时仍继续定位记录', () => {
        const intent = { branchId: 'invalid-branch', targetRecordId: 'record-123' }
        const result = processJumpIntent(intent, null, () => null)
        expect(result.switched).toBe(false)
        expect(result.locateId).toBe('record-123')
      })

      it('无跳转意图时不做任何操作', () => {
        const result = processJumpIntent(null, null, (_id) => ({
          success: true,
          branchName: '测试分支'
        }))
        expect(result.switched).toBe(false)
        expect(result.locateId).toBeUndefined()
      })
    })
  })

  /**
   * 复刻 DynamicPage 标题栏「切换分支」下拉与「管理版本」项的显隐规则。
   * 后端：切换分支 = @write_required（仅拦截访客）；管理版本 = @require_permission('admin.project_versions')。
   * 前端门禁必须与后端一致：开发(developer) 角色可切换分支，但不显示管理版本入口。
   */
  function canShowBranchSwitch(opts: { isGuest: boolean; projectMenuId: string | null }): boolean {
    return !opts.isGuest && !!opts.projectMenuId
  }
  function canManageVersions(isAdmin: boolean): boolean {
    return isAdmin
  }

  describe('DynamicPage — 分支切换按钮显隐', () => {
    it('开发角色（非访客非管理员）在存在项目时应能看到切换分支按钮', () => {
      expect(canShowBranchSwitch({ isGuest: false, projectMenuId: 'menu-proj-1' })).toBe(true)
    })

    it('访客不显示切换分支按钮', () => {
      expect(canShowBranchSwitch({ isGuest: true, projectMenuId: 'menu-proj-1' })).toBe(false)
    })

    it('不属于任何项目时不显示切换分支按钮', () => {
      expect(canShowBranchSwitch({ isGuest: false, projectMenuId: null })).toBe(false)
    })

    it('「管理版本」入口仅管理员可见', () => {
      expect(canManageVersions(true)).toBe(true)
      expect(canManageVersions(false)).toBe(false)
    })
  })
})
