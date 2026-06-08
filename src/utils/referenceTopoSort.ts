import type { FieldConfig, PageConfig } from '@/types'

export interface TopoResult {
  /** collection 导入顺序：被引用的排在引用方之前 */
  order: string[]
  /** 检测到的环（残留无法定序的 collection 分组）；无环为空数组 */
  cycles: string[][]
}

function collOf(cfg: PageConfig): string {
  return cfg.id.replace(/^page-/, '')
}

/**
 * 基于 reference / quoteSelect 字段的 targetCollection 构建依赖图并拓扑排序。
 * 边方向：target → page（target 必须先导入）。
 * 仅考虑目标也在入参集合内的引用；自引用与外部引用忽略。
 */
export function buildReferenceOrder(configs: PageConfig[]): TopoResult {
  const collections = configs.map(collOf)
  const collSet = new Set(collections)

  const dependents = new Map<string, Set<string>>() // node → 依赖它的页集合
  const indeg = new Map<string, number>()
  for (const c of collections) {
    dependents.set(c, new Set())
    indeg.set(c, 0)
  }

  for (const cfg of configs) {
    const coll = collOf(cfg)
    for (const f of (cfg.fields || []) as FieldConfig[]) {
      let target: string | undefined
      if (f.controlType === 'reference') target = f.referenceConfig?.targetCollection
      else if (f.controlType === 'quoteSelect') target = f.quoteConfig?.targetCollection
      if (!target || target === coll || !collSet.has(target)) continue
      if (!dependents.get(target)!.has(coll)) {
        dependents.get(target)!.add(coll)
        indeg.set(coll, (indeg.get(coll) || 0) + 1)
      }
    }
  }

  const queue = collections.filter((c) => (indeg.get(c) || 0) === 0)
  const order: string[] = []
  while (queue.length) {
    const n = queue.shift()!
    order.push(n)
    for (const m of dependents.get(n)!) {
      indeg.set(m, indeg.get(m)! - 1)
      if (indeg.get(m) === 0) queue.push(m)
    }
  }

  const cycles: string[][] = []
  if (order.length < collections.length) {
    const ordered = new Set(order)
    const remaining = collections.filter((c) => !ordered.has(c))
    cycles.push(remaining)
    for (const c of remaining) order.push(c) // 断边尽力：残留追加到末尾
  }

  return { order, cycles }
}
