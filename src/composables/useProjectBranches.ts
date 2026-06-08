import { ref } from 'vue'
import { getAllBranches } from '@/api/projectVersion'

export interface BranchChoice {
  id: string
  name: string
}

/**
 * 加载分支下拉选项。
 * - 传 projectMenuId：只返回该项目的活跃分支（标签为分支名），始终含主分支。
 * - 不传：返回所有项目的活跃分支（标签带项目名前缀），始终含主分支。
 */
export function useProjectBranches() {
  const branchOptions = ref<BranchChoice[]>([{ id: 'main', name: '主分支' }])

  async function loadBranches(projectMenuId?: string): Promise<void> {
    try {
      const all = await getAllBranches()
      const opts: BranchChoice[] = [{ id: 'main', name: '主分支' }]
      for (const b of all) {
        if (b.id === 'main') continue
        if (projectMenuId && b.projectMenuId !== projectMenuId) continue
        opts.push({
          id: b.id,
          name: projectMenuId ? b.name : `${b.projectName ?? ''} / ${b.name}`,
        })
      }
      branchOptions.value = opts
    } catch {
      branchOptions.value = [{ id: 'main', name: '主分支' }]
    }
  }

  return { branchOptions, loadBranches }
}
