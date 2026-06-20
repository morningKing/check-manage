/**
 * 关联/引用/quoteSelect 选择器的「服务端搜索式」选项加载。
 *
 * 不再一次性把整张目标数据页拉到前端再本地过滤（大集合会很重），改为：
 *  - 初始只取前 N 条；
 *  - 用户输入时防抖查后端 keyword 全文搜索，只回 Top N 命中；
 *  - 已选中的值（编辑场景）用后端 ?ids= 单独回填标签，保证即使不在搜索结果里也能正确显示。
 *
 * 这些控件的 value 恒为目标记录 id，故回填走 ids 列过滤。
 */
import { ref } from 'vue'
import { get } from '@/utils/request'

export interface RemoteOption { label: string; value: string }

export function useRemoteCollectionOptions(cfg: {
  collection: () => string | undefined
  labelField: () => string
  pageSize?: number
  debounceMs?: number
}) {
  const options = ref<RemoteOption[]>([])
  const loading = ref(false)
  // 已选中值的选项，恒保留，使其标签不随搜索结果变化而消失
  const pinned = new Map<string, RemoteOption>()
  let searched: RemoteOption[] = []
  let timer: ReturnType<typeof setTimeout> | null = null

  const labelOf = (item: any) => String(item[cfg.labelField()] ?? item.id ?? '')

  function rebuild() {
    const map = new Map(pinned)
    for (const o of searched) if (!map.has(o.value)) map.set(o.value, o)
    options.value = [...map.values()]
  }

  async function runSearch(query: string) {
    const coll = cfg.collection()
    if (!coll) { searched = []; rebuild(); return }
    loading.value = true
    try {
      const params: Record<string, any> = { pageSize: cfg.pageSize ?? 50 }
      if (query && query.trim()) params.keyword = query.trim()
      const resp = await get<{ data: any[] }>(`/${coll}`, params)
      searched = (resp.data || []).map((it) => ({ label: labelOf(it), value: String(it.id) }))
      rebuild()
    } catch {
      searched = []
      rebuild()
    } finally {
      loading.value = false
    }
  }

  /** el-select 的 remote-method：防抖后查后端 */
  function onSearch(query: string) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { void runSearch(query) }, cfg.debounceMs ?? 300)
  }

  /** 回填已选中值的标签（编辑场景或用户刚选中）。未命中的退化为「裸 id 当标签」。 */
  async function ensureSelectedLabels(values: Array<string | null | undefined>) {
    const coll = cfg.collection()
    const ids = [...new Set(values.filter((v): v is string => !!v))]
    let missing = ids.filter((id) => !pinned.has(id))
    if (missing.length === 0) { rebuild(); return }
    // 先从已有搜索结果里捡，省一次请求
    const inSearched = new Map(searched.map((o) => [o.value, o]))
    missing = missing.filter((id) => {
      const o = inSearched.get(id)
      if (o) { pinned.set(id, o); return false }
      return true
    })
    if (coll && missing.length > 0) {
      const CHUNK = 100
      try {
        for (let i = 0; i < missing.length; i += CHUNK) {
          const chunk = missing.slice(i, i + CHUNK)
          const resp = await get<{ data: any[] }>(`/${coll}`, { ids: chunk.join(','), all: true })
          for (const it of (resp.data || [])) {
            const v = String(it.id)
            pinned.set(v, { label: labelOf(it), value: v })
          }
        }
      } catch { /* 失败则下方退化为裸 id */ }
    }
    for (const id of missing) if (!pinned.has(id)) pinned.set(id, { label: id, value: id })
    rebuild()
  }

  /** 挂载时：取首屏选项 + 回填已选标签 */
  async function init(selected: Array<string | null | undefined>) {
    await Promise.all([runSearch(''), ensureSelectedLabels(selected)])
  }

  return { options, loading, onSearch, ensureSelectedLabels, init }
}
