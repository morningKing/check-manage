/**
 * 工作流引擎相关类型定义
 *
 * 一个工作流定义由若干有序的阶段（stage）组成，每个阶段绑定一个数据页（collection），
 * 并可配置状态字段、推进/回退转换、办理角色，以及生成下游记录（spawn）的字段映射。
 */

export interface WorkflowStage {
  id: string
  name: string
  collection: string
  statusField?: string
  /** 推进转换：状态字段从 from → to */
  advanceTransition?: { from: string; to: string }
  /** 可选回退转换：状态字段从 from → to */
  rejectTransition?: { from: string; to: string }
  /** 可办理该阶段的角色 slug 列表 */
  assignedRoles?: string[]
  /** 生成下游记录：目标字段 → 取值表达式（$source.<上游字段> / $NOW / 字面量） */
  spawn?: { fieldMapping: Record<string, string>; linkBackField?: string }
}

export interface WorkflowDefinition {
  id?: string
  name: string
  description?: string
  enabled: boolean
  stages: WorkflowStage[]
  /** 保存时后端返回的非阻断配置告警（如某阶段状态字段无法驱动推进） */
  warnings?: string[]
}

export interface WorkflowInboxItem {
  instanceId: string
  workflowName: string
  stageName: string
  collection: string | null
  recordId: string | null
  enteredAt: string | null
}
