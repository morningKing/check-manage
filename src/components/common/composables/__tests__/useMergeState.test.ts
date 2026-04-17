/**
 * useMergeState Composable 单元测试
 *
 * 测试合并状态管理的核心功能：
 * - 状态初始化
 * - 差异检测 (hasDiff, hasSelection)
 * - 记录选择/取消选择
 * - 字段级决策
 * - 全选/批量操作
 */
import { describe, it, expect } from 'vitest'
import { useMergeState } from '../useMergeState'
import type { CollectionVersion, MergeDecisions } from '@/types/version'
import type { DiffResult } from '@/types/backup'

function createMockVersion(overrides: Partial<CollectionVersion> = {}): CollectionVersion {
  return {
    id: 'version-1',
    collection: 'test-collection',
    name: 'Test Version',
    versionType: 'snapshot',
    status: 'active',
    dataHash: 'hash123',
    recordsCount: 10,
    relationsCount: 0,
    createdBy: 'admin',
    createdAt: '2024-01-01T00:00:00Z',
    isProtected: false,
    collections: ['test-collection'],
    ...overrides,
  }
}

function createMockDiffResult(overrides: Partial<DiffResult> = {}): DiffResult {
  return {
    added: [],
    removed: [],
    modified: [],
    unchangedCount: 0,
    fields: [],
    ...overrides,
  }
}

describe('useMergeState', () => {
  describe('初始状态', () => {
    it('初始状态应为空', () => {
      const { state, hasDiff, hasSelection, selectedCount } = useMergeState()

      expect(state.sourceVersion).toBeNull()
      expect(state.diffResult).toBeNull()
      expect(state.decisions.addedRecords.size).toBe(0)
      expect(state.decisions.removedRecords.size).toBe(0)
      expect(state.decisions.modifiedRecords.size).toBe(0)
      expect(state.expandedRecords.size).toBe(0)
      expect(hasDiff.value).toBe(false)
      expect(hasSelection.value).toBe(false)
      expect(selectedCount.value).toBe(0)
    })
  })

  describe('hasDiff vs hasSelection 区分', () => {
    it('hasDiff 检测差异结果是否存在', () => {
      const { setDiffResult, hasDiff } = useMergeState()

      // 无差异
      setDiffResult(createMockDiffResult())
      expect(hasDiff.value).toBe(false)

      // 有新增记录
      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增记录' }],
      }))
      expect(hasDiff.value).toBe(true)

      // 有删除记录
      setDiffResult(createMockDiffResult({
        removed: [{ id: '2', name: '删除记录' }],
      }))
      expect(hasDiff.value).toBe(true)

      // 有修改记录
      setDiffResult(createMockDiffResult({
        modified: [{
          id: '3',
          record: { name: '新值' },
          oldRecord: { name: '旧值' },
          fields: [{ fieldName: 'name', oldValue: '旧值', newValue: '新值' }],
        }],
      }))
      expect(hasDiff.value).toBe(true)
    })

    it('hasSelection 检测用户是否已选择变更', () => {
      const { setDiffResult, hasSelection, toggleAddedRecord, toggleRemovedRecord, toggleModifiedRecord } = useMergeState()

      // 设置差异
      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增' }],
        removed: [{ id: '2', name: '删除' }],
        modified: [{
          id: '3',
          record: { name: '新' },
          oldRecord: { name: '旧' },
          fields: [{ fieldName: 'name', oldValue: '旧', newValue: '新' }],
        }],
      }))

      // 差异存在但未选择
      expect(hasSelection.value).toBe(false)

      // 选择新增记录
      toggleAddedRecord('1')
      expect(hasSelection.value).toBe(true)

      // 取消选择
      toggleAddedRecord('1')
      expect(hasSelection.value).toBe(false)

      // 选择删除记录
      toggleRemovedRecord('2')
      expect(hasSelection.value).toBe(true)

      // 取消选择
      toggleRemovedRecord('2')
      expect(hasSelection.value).toBe(false)

      // 选择修改记录
      toggleModifiedRecord('3')
      expect(hasSelection.value).toBe(true)
    })

    it('有差异但未选择时 hasDiff=true, hasSelection=false', () => {
      const { setDiffResult, hasDiff, hasSelection } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增' }],
        modified: [{
          id: '2',
          record: {},
          oldRecord: {},
          fields: [{ fieldName: 'name', oldValue: '旧', newValue: '新' }],
        }],
      }))

      expect(hasDiff.value).toBe(true)
      expect(hasSelection.value).toBe(false)
    })

    it('有差异且已选择时 hasDiff=true, hasSelection=true', () => {
      const { setDiffResult, toggleAddedRecord, hasDiff, hasSelection } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增' }],
      }))
      toggleAddedRecord('1')

      expect(hasDiff.value).toBe(true)
      expect(hasSelection.value).toBe(true)
    })
  })

  describe('记录选择操作', () => {
    it('toggleAddedRecord 切换新增记录选择状态', () => {
      const { setDiffResult, toggleAddedRecord, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1' }, { id: '2' }],
      }))

      toggleAddedRecord('1')
      expect(state.decisions.addedRecords.has('1')).toBe(true)
      expect(state.decisions.addedRecords.size).toBe(1)

      toggleAddedRecord('1')
      expect(state.decisions.addedRecords.has('1')).toBe(false)
      expect(state.decisions.addedRecords.size).toBe(0)
    })

    it('toggleRemovedRecord 切换删除记录选择状态', () => {
      const { setDiffResult, toggleRemovedRecord, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        removed: [{ id: '1' }, { id: '2' }],
      }))

      toggleRemovedRecord('1')
      expect(state.decisions.removedRecords.has('1')).toBe(true)

      toggleRemovedRecord('1')
      expect(state.decisions.removedRecords.has('1')).toBe(false)
    })

    it('toggleModifiedRecord 切换修改记录选择状态并初始化字段决策', () => {
      const { setDiffResult, toggleModifiedRecord, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        modified: [{
          id: '1',
          record: { name: '新', status: 'active' },
          oldRecord: { name: '旧', status: 'inactive' },
          fields: [
            { fieldName: 'name', oldValue: '旧', newValue: '新' },
            { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
          ],
        }],
      }))

      toggleModifiedRecord('1')

      expect(state.decisions.modifiedRecords.has('1')).toBe(true)
      const decision = state.decisions.modifiedRecords.get('1')
      expect(decision).toBeDefined()
      expect(decision!.fieldDecisions.size).toBe(2)
      expect(decision!.fieldDecisions.get('name')).toBe('source') // 默认选择源版本
      expect(decision!.fieldDecisions.get('status')).toBe('source')
    })

    it('取消选择修改记录时移除展开状态', () => {
      const { setDiffResult, toggleModifiedRecord, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        modified: [{
          id: '1',
          record: {},
          oldRecord: {},
          fields: [{ fieldName: 'name', oldValue: '旧', newValue: '新' }],
        }],
      }))

      toggleModifiedRecord('1')
      // toggleModifiedRecord now auto-expands
      expect(state.expandedRecords.has('1')).toBe(true)

      toggleModifiedRecord('1') // 取消选择
      expect(state.decisions.modifiedRecords.has('1')).toBe(false)
      expect(state.expandedRecords.has('1')).toBe(false)
    })
  })

  describe('字段级决策', () => {
    it('setFieldDecision 设置单个字段选择', () => {
      const { setDiffResult, toggleModifiedRecord, setFieldDecision, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        modified: [{
          id: '1',
          record: {},
          oldRecord: {},
          fields: [
            { fieldName: 'name', oldValue: '旧', newValue: '新' },
            { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
          ],
        }],
      }))

      toggleModifiedRecord('1')
      setFieldDecision('1', 'status', 'target')

      const decision = state.decisions.modifiedRecords.get('1')
      expect(decision!.fieldDecisions.get('status')).toBe('target')
      expect(decision!.fieldDecisions.get('name')).toBe('source')
    })

    it('setAllFieldsForRecord 设置记录所有字段选择', () => {
      const { setDiffResult, toggleModifiedRecord, setAllFieldsForRecord, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        modified: [{
          id: '1',
          record: {},
          oldRecord: {},
          fields: [
            { fieldName: 'name', oldValue: '旧', newValue: '新' },
            { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
          ],
        }],
      }))

      toggleModifiedRecord('1')
      setAllFieldsForRecord('1', 'target')

      const decision = state.decisions.modifiedRecords.get('1')
      expect(decision!.fieldDecisions.get('name')).toBe('target')
      expect(decision!.fieldDecisions.get('status')).toBe('target')
    })
  })

  describe('全选操作', () => {
    it('selectAllAdded 全选/取消全选新增记录', () => {
      const { setDiffResult, selectAllAdded, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1' }, { id: '2' }, { id: '3' }],
      }))

      selectAllAdded(true)
      expect(state.decisions.addedRecords.size).toBe(3)
      expect(state.decisions.addedRecords.has('1')).toBe(true)
      expect(state.decisions.addedRecords.has('2')).toBe(true)
      expect(state.decisions.addedRecords.has('3')).toBe(true)

      selectAllAdded(false)
      expect(state.decisions.addedRecords.size).toBe(0)
    })

    it('selectAllRemoved 全选/取消全选删除记录', () => {
      const { setDiffResult, selectAllRemoved, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        removed: [{ id: '1' }, { id: '2' }],
      }))

      selectAllRemoved(true)
      expect(state.decisions.removedRecords.size).toBe(2)

      selectAllRemoved(false)
      expect(state.decisions.removedRecords.size).toBe(0)
    })

    it('selectAllModified 全选/取消全选修改记录', () => {
      const { setDiffResult, selectAllModified, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        modified: [
          { id: '1', record: {}, oldRecord: {}, fields: [{ fieldName: 'name', oldValue: '旧', newValue: '新' }] },
          { id: '2', record: {}, oldRecord: {}, fields: [{ fieldName: 'status', oldValue: 'a', newValue: 'b' }] },
        ],
      }))

      selectAllModified(true)
      expect(state.decisions.modifiedRecords.size).toBe(2)

      selectAllModified(false)
      expect(state.decisions.modifiedRecords.size).toBe(0)
      expect(state.expandedRecords.size).toBe(0)
    })
  })

  describe('批量接受操作', () => {
    it('acceptAllSource 接受所有源版本变更', () => {
      const { setDiffResult, acceptAllSource, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增' }],
        removed: [{ id: '2', name: '删除' }],
        modified: [{
          id: '3',
          record: {},
          oldRecord: {},
          fields: [
            { fieldName: 'name', oldValue: '旧', newValue: '新' },
            { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
          ],
        }],
      }))

      acceptAllSource()

      expect(state.decisions.addedRecords.has('1')).toBe(true)
      expect(state.decisions.removedRecords.has('2')).toBe(true)
      expect(state.decisions.modifiedRecords.has('3')).toBe(true)

      const decision = state.decisions.modifiedRecords.get('3')
      expect(decision!.fieldDecisions.get('name')).toBe('source')
      expect(decision!.fieldDecisions.get('status')).toBe('source')
    })

    it('acceptAllTarget 接受所有目标版本（丢弃新增和删除）', () => {
      const { setDiffResult, acceptAllTarget, state } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1', name: '新增' }],
        removed: [{ id: '2', name: '删除' }],
        modified: [{
          id: '3',
          record: {},
          oldRecord: {},
          fields: [
            { fieldName: 'name', oldValue: '旧', newValue: '新' },
            { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
          ],
        }],
      }))

      acceptAllTarget()

      // 接受目标版本意味着不添加新记录、不删除旧记录
      expect(state.decisions.addedRecords.size).toBe(0)
      expect(state.decisions.removedRecords.size).toBe(0)

      // 修改记录使用目标版本值
      expect(state.decisions.modifiedRecords.has('3')).toBe(true)
      const decision = state.decisions.modifiedRecords.get('3')
      expect(decision!.fieldDecisions.get('name')).toBe('target')
      expect(decision!.fieldDecisions.get('status')).toBe('target')
    })
  })

  describe('展开状态', () => {
    it('toggleRecordExpanded 切换展开状态', () => {
      const { toggleRecordExpanded, state } = useMergeState()

      toggleRecordExpanded('1')
      expect(state.expandedRecords.has('1')).toBe(true)

      toggleRecordExpanded('1')
      expect(state.expandedRecords.has('1')).toBe(false)
    })
  })

  describe('selectedCount 计算', () => {
    it('正确计算已选择的变更总数', () => {
      const { setDiffResult, toggleAddedRecord, toggleRemovedRecord, toggleModifiedRecord, selectedCount } = useMergeState()

      setDiffResult(createMockDiffResult({
        added: [{ id: '1' }, { id: '2' }],
        removed: [{ id: '3' }],
        modified: [{
          id: '4',
          record: {},
          oldRecord: {},
          fields: [{ fieldName: 'name', oldValue: '旧', newValue: '新' }],
        }],
      }))

      expect(selectedCount.value).toBe(0)

      toggleAddedRecord('1')
      expect(selectedCount.value).toBe(1)

      toggleAddedRecord('2')
      expect(selectedCount.value).toBe(2)

      toggleRemovedRecord('3')
      expect(selectedCount.value).toBe(3)

      toggleModifiedRecord('4')
      expect(selectedCount.value).toBe(4)
    })
  })

  describe('setSourceVersion', () => {
    it('设置源版本信息', () => {
      const { setSourceVersion, state } = useMergeState()
      const version = createMockVersion()

      setSourceVersion(version)
      expect(state.sourceVersion).toStrictEqual(version)
    })
  })

  describe('setDiffResult', () => {
    it('设置差异结果并重置决策', () => {
      const { setDiffResult, toggleAddedRecord, state } = useMergeState()

      // 先设置差异并选择
      setDiffResult(createMockDiffResult({ added: [{ id: '1' }] }))
      toggleAddedRecord('1')
      expect(state.decisions.addedRecords.size).toBe(1)

      // 设置新的差异结果应重置决策
      setDiffResult(createMockDiffResult({ added: [{ id: '2' }] }))
      expect(state.decisions.addedRecords.size).toBe(0)
      expect(state.expandedRecords.size).toBe(0)
    })
  })

  describe('setDecisions', () => {
    it('设置整个决策对象', () => {
      const { setDecisions, state } = useMergeState()

      const decisions: MergeDecisions = {
        addedRecords: new Set(['a1', 'a2']),
        removedRecords: new Set(['r1']),
        modifiedRecords: new Map([
          ['m1', { recordId: 'm1', fieldDecisions: new Map([['name', 'source']]) }],
        ]),
      }

      setDecisions(decisions)

      expect(state.decisions.addedRecords.has('a1')).toBe(true)
      expect(state.decisions.addedRecords.has('a2')).toBe(true)
      expect(state.decisions.removedRecords.has('r1')).toBe(true)
      expect(state.decisions.modifiedRecords.has('m1')).toBe(true)
    })
  })

  describe('reset', () => {
    it('重置所有状态到初始值', () => {
      const {
        setSourceVersion,
        setDiffResult,
        toggleAddedRecord,
        toggleRecordExpanded,
        reset,
        state,
      } = useMergeState()

      // 设置一些状态
      setSourceVersion(createMockVersion())
      setDiffResult(createMockDiffResult({ added: [{ id: '1' }] }))
      toggleAddedRecord('1')
      toggleRecordExpanded('1')

      // 重置
      reset()

      expect(state.sourceVersion).toBeNull()
      expect(state.diffResult).toBeNull()
      expect(state.decisions.addedRecords.size).toBe(0)
      expect(state.expandedRecords.size).toBe(0)
    })
  })

  describe('buildMergePayload', () => {
    it('构建合并请求负载', () => {
      const { setSourceVersion, setDiffResult, toggleAddedRecord, toggleRemovedRecord, toggleModifiedRecord, setFieldDecision, buildMergePayload } = useMergeState()

      setSourceVersion(createMockVersion({ id: 'v1' }))
      setDiffResult(createMockDiffResult({
        added: [{ id: 'add1' }],
        removed: [{ id: 'del1' }],
        modified: [{
          id: 'mod1',
          record: { name: '新值' },
          oldRecord: { name: '旧值' },
          fields: [{ fieldName: 'name', oldValue: '旧值', newValue: '新值' }],
        }],
      }))

      toggleAddedRecord('add1')
      toggleRemovedRecord('del1')
      toggleModifiedRecord('mod1')
      setFieldDecision('mod1', 'name', 'target') // 选择目标版本

      const payload = buildMergePayload()

      expect(payload).not.toBeNull()
      expect(payload!.source_version_id).toBe('v1')
      expect(payload!.target_branch).toBe('current')
      expect(payload!.decisions.added_record_ids).toContain('add1')
      expect(payload!.decisions.removed_record_ids).toContain('del1')
      expect(payload!.decisions.modified_records).toHaveLength(1)
      expect(payload!.decisions.modified_records[0].record_id).toBe('mod1')
      expect(payload!.decisions.modified_records[0].field_values.name).toBe('旧值') // 选择目标版本
    })

    it('未设置源版本时返回 null', () => {
      const { buildMergePayload } = useMergeState()
      expect(buildMergePayload()).toBeNull()
    })
  })
})