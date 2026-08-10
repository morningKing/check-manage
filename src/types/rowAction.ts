/**
 * 数据页自定义行级操作按钮的类型定义
 *
 * 配置存在 page_configs.row_actions（JSONB 列），只描述「哪个按钮、绑哪个执行器、
 * 怎么显示、怎么回写」这层薄绑定；真正的执行器（Webhook 规则 / AI 扫描任务）
 * 仍是全局资源，各有自己的管理页。
 */

import type { FieldConfig } from './field'
import type { FieldMappingRow } from './aiScanTask'

/** 动作类型：绑 Webhook 规则，或绑 AI 扫描任务 */
export type RowActionType = 'webhook' | 'aiTask'

/** 单条可见性条件。前后端各求值一次，语义由共享夹具锁定 */
export interface RowActionCondition {
  field: string
  operator: 'eq' | 'ne' | 'in' | 'notIn' | 'empty' | 'notEmpty'
  /** eq/ne 用 string；in/notIn 用 string[]；empty/notEmpty 不用 */
  value?: string | string[]
}

export interface RowActionConfig {
  /** nanoid，页面内唯一 */
  id: string
  /** 菜单文案，必填 */
  label: string
  actionType: RowActionType
  enabled: boolean

  /** actionType='webhook' 时必填 */
  webhookRuleId?: string
  /** actionType='aiTask' 时必填 */
  scanTaskId?: string

  /** 空/缺省 = 不限角色 */
  roles?: string[]
  /** 缺省 = 总是显示 */
  visibleWhen?: RowActionCondition
  /** 缺省 = 不二次确认 */
  confirmText?: string
  /** 缺省 = 不弹参数表单 */
  paramFields?: FieldConfig[]

  /** 缺省 = 不写状态 */
  statusField?: string
  runningValue?: string
  doneValue?: string
  failedValue?: string
  /** 仅 webhook 生效：把响应 JSON 按映射写回字段 */
  responseMapping?: FieldMappingRow[]
}

/** 执行接口的返回体 */
export interface RunRowActionResult {
  ok: boolean
  /** 'running' = 已写入 runningValue，前端可轮询；'submitted' = 无状态字段，不轮询 */
  status: 'running' | 'submitted'
  /**
   * status='running' 时，该动作实际生效的状态字段/执行中值（AI 动作用所绑扫描
   * 任务的配置，见后端 resolve_status_gate）。RowActionRunner 轮询时用它判断
   * 该行是否已离开执行中态，离开就立即停止轮询，而不是盲等到 5 分钟上限。
   * 缺省（后端解析失败等极端情况）时退回旧的盲等轮询。
   */
  statusField?: string | null
  runningValue?: string | null
}
