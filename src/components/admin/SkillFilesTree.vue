<template>
  <div v-loading="loading" class="skill-files-tree" data-test="skill-files-tree">
    <div class="skill-files-tree__bar">
      <el-input v-model="keyword" size="small" clearable
                class="skill-files-tree__search" data-test="file-search"
                placeholder="按文件名/路径筛选…" />
      <el-button size="small" data-test="expand-all" @click="expandAll">展开全部</el-button>
      <el-button size="small" data-test="collapse-all" @click="collapseAll">折叠全部</el-button>
      <div class="skill-files-tree__extra"><slot name="toolbar" /></div>
    </div>
    <div v-if="!files.length" class="skill-files-tree__empty">无文件</div>
    <template v-else>
      <div v-if="!rows.length" class="skill-files-tree__empty">没有匹配的文件</div>
      <div v-for="row in rows" :key="row.id" class="skill-files-tree__row"
           :class="{ 'is-dir': row.isDir }" :data-path="row.file?.path || ''"
           :data-dir="row.isDir ? row.label : undefined" @click="onRowClick(row)">
        <span class="skill-files-tree__name" :style="{ paddingLeft: `${row.depth * 16}px` }">
          <span class="skill-files-tree__caret">{{ row.isDir ? (expandedIds.has(row.id) ? '▾' : '▸') : '' }}</span>
          <span class="skill-files-tree__label">{{ row.label }}</span>
        </span>
        <span v-if="row.isDir" class="skill-files-tree__count">{{ row.fileCount }} 个文件</span>
        <template v-else>
          <span class="skill-files-tree__path">{{ row.file!.path }}</span>
          <span class="skill-files-tree__size">{{ formatSize(row.file!.size) }}</span>
          <span class="skill-files-tree__time">{{ formatTime(row.file!.mtime) }}</span>
          <span class="skill-files-tree__actions" @click.stop>
            <slot name="actions" :file="row.file!" />
          </span>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'

/**
 * 技能文件树（2026-10-15 运行时可编辑化改造）：
 * 平铺文件列表 → 目录树。默认只展开根层（根级文件 + 顶层文件夹各一行），
 * 文件夹点击展开/收起；搜索按文件名/路径过滤并自动展开命中路径；
 * 展开全部/折叠全部一键；保留 大小/修改时间 列（列在嵌套下仍对齐——
 * 缩进只作用在名称格）。运行时「技能」tab 与「平台技能」面板共用。
 */

export interface SkillFileItem {
  name: string
  path: string
  size: number
  /** 秒级时间戳（runtime 侧）或 ISO 串（平台侧）；缺省显示 — */
  mtime?: number | string | null
}

const props = withDefaults(defineProps<{
  files: SkillFileItem[]
  loading?: boolean
}>(), { loading: false })

const emit = defineEmits<{ (e: 'select', file: SkillFileItem): void }>()

interface TreeNode {
  id: string
  label: string
  isDir: boolean
  depth: number
  file?: SkillFileItem
  children: TreeNode[]
  fileCount: number
}

interface Row {
  id: string
  label: string
  isDir: boolean
  depth: number
  file?: SkillFileItem
  fileCount: number
}

const keyword = ref('')
const expandedIds = ref(new Set<string>())

/** 平铺路径 → 目录树（文件夹在前、按名称排；文件夹带递归文件数） */
const tree = computed<TreeNode[]>(() => {
  const root: TreeNode[] = []
  const dirMap = new Map<string, TreeNode>()
  const node = (id: string, label: string, isDir: boolean, depth: number,
                file?: SkillFileItem): TreeNode =>
    ({ id, label, isDir, depth, file, children: [], fileCount: 0 })

  for (const f of props.files) {
    const segs = f.path.split('/').filter(Boolean)
    if (!segs.length) continue
    let level = root
    let acc = ''
    for (let i = 0; i < segs.length - 1; i++) {
      acc = acc ? `${acc}/${segs[i]}` : segs[i]
      let dir = dirMap.get(acc)
      if (!dir) {
        dir = node(`d:${acc}`, segs[i], true, i)
        dirMap.set(acc, dir)
        level.push(dir)
      }
      level = dir.children
    }
    level.push(node(`f:${f.path}`, segs[segs.length - 1], false,
                    segs.length - 1, f))
  }

  const sortLevel = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.isDir === b.isDir
      ? a.label.localeCompare(b.label)
      : (a.isDir ? -1 : 1))
    nodes.forEach(n => sortLevel(n.children))
  }
  sortLevel(root)

  const count = (n: TreeNode): number => {
    n.fileCount = n.isDir
      ? n.children.reduce((s, c) => s + count(c), 0)
      : 1
    return n.fileCount
  }
  root.forEach(count)
  return root
})

/** 搜索命中集合（文件命中 + 其祖先目录）；空关键字 → null（不过滤） */
const matching = computed<Set<string> | null>(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return null
  const keep = new Set<string>()
  const walk = (nodes: TreeNode[]): boolean => {
    let any = false
    for (const n of nodes) {
      if (n.isDir) {
        if (walk(n.children)) {
          keep.add(n.id)
          any = true
        }
      } else if (n.label.toLowerCase().includes(kw)
                 || (n.file?.path.toLowerCase().includes(kw) ?? false)) {
        keep.add(n.id)
        any = true
      }
    }
    return any
  }
  walk(tree.value)
  return keep
})

/** 可见行（折叠状态 + 过滤；过滤时命中目录自动展开） */
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const keep = matching.value
  const walk = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      if (keep && !keep.has(n.id)) continue
      out.push({ id: n.id, label: n.label, isDir: n.isDir, depth: n.depth,
                 file: n.file, fileCount: n.fileCount })
      if (n.isDir && (keep || expandedIds.value.has(n.id))) walk(n.children)
    }
  }
  walk(tree.value)
  return out
})

function toggleDir(id: string) {
  const next = new Set(expandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedIds.value = next
}

function allDirIds(): string[] {
  const ids: string[] = []
  const walk = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      if (n.isDir) {
        ids.push(n.id)
        walk(n.children)
      }
    }
  }
  walk(tree.value)
  return ids
}

function expandAll() {
  expandedIds.value = new Set(allDirIds())
}

function collapseAll() {
  expandedIds.value = new Set()
}

/** 展开并定位到某文件所在目录（编辑/新建返回后调用；搜索框清空以保命中） */
function revealPath(path: string) {
  keyword.value = ''
  const segs = path.split('/').filter(Boolean)
  const next = new Set(expandedIds.value)
  let acc = ''
  for (let i = 0; i < segs.length - 1; i++) {
    acc = acc ? `${acc}/${segs[i]}` : segs[i]
    next.add(`d:${acc}`)
  }
  expandedIds.value = next
}

function onRowClick(row: Row) {
  if (row.isDir) toggleDir(row.id)
  else if (row.file) emit('select', row.file)
}

function formatSize(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(ts: number | string | null | undefined): string {
  if (ts == null || ts === '') return '—'
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('zh-CN', { hour12: false })
}

defineExpose({ expandAll, collapseAll, revealPath })
</script>

<style scoped lang="scss">
.skill-files-tree {
  &__bar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  &__search { width: 200px; }
  &__extra {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }
  &__empty {
    color: var(--el-text-color-secondary);
    padding: 24px 0;
    text-align: center;
  }
  &__row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 6px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    &:hover { background: var(--el-fill-color-light); }
    &.is-dir { font-weight: 500; }
  }
  &__name {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }
  &__caret {
    display: inline-block;
    width: 14px;
    color: var(--el-text-color-secondary);
    flex: none;
  }
  &__label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  &__count {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    flex: none;
  }
  &__path {
    color: var(--el-text-color-secondary);
    font-family: monospace;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 34%;
    flex: none;
  }
  &__size {
    width: 80px;
    text-align: right;
    color: var(--el-text-color-secondary);
    flex: none;
  }
  &__time {
    width: 150px;
    color: var(--el-text-color-secondary);
    flex: none;
  }
  &__actions { flex: none; }
}
</style>
