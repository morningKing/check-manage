/**
 * SkillFilesTree 组件测试（2026-10-15 运行时可编辑化）：
 * 默认收起只根层 / 点击展开收起 / 展开折叠全部 / 搜索过滤+自动展开 /
 * revealPath / select 事件 / 大小与时间列。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SkillFilesTree from '../SkillFilesTree.vue'

const FILES = [
  { name: 'SKILL.md', path: 'SKILL.md', size: 100, mtime: 1700000000 },
  { name: 'run.py', path: 'scripts/run.py', size: 2048, mtime: 1700000000 },
  { name: 'util.py', path: 'scripts/lib/util.py', size: 300, mtime: null },
  { name: 'a.md', path: 'templates/a.md', size: 50, mtime: 1700000000 },
]

function mountTree(extra: Record<string, unknown> = {}) {
  return mount(SkillFilesTree, { props: { files: FILES, ...extra } })
}

const row = (w: ReturnType<typeof mountTree>, path: string) =>
  w.find(`[data-path="${path}"]`)
const dirRow = (w: ReturnType<typeof mountTree>, name: string) =>
  w.find(`[data-dir="${name}"]`)
const has = (w: ReturnType<typeof mountTree>, path: string) => row(w, path).exists()

describe('SkillFilesTree', () => {
  it('默认只展开根层：根级文件与顶层文件夹可见，子文件收起', () => {
    const w = mountTree()
    expect(has(w, 'SKILL.md')).toBe(true)
    expect(dirRow(w, 'scripts').exists()).toBe(true)
    expect(dirRow(w, 'templates').exists()).toBe(true)
    expect(has(w, 'scripts/run.py')).toBe(false)   // 未展开
    expect(has(w, 'scripts/lib/util.py')).toBe(false)
    expect(w.text()).toContain('2 个文件')          // scripts 递归计数
  })

  it('点击文件夹展开/收起，子级逐层出现', async () => {
    const w = mountTree()
    await dirRow(w, 'scripts').trigger('click')
    expect(has(w, 'scripts/run.py')).toBe(true)
    expect(has(w, 'scripts/lib/util.py')).toBe(false)   // lib 仍收起
    await dirRow(w, 'lib').trigger('click')
    expect(has(w, 'scripts/lib/util.py')).toBe(true)
    await dirRow(w, 'scripts').trigger('click')          // 收起 scripts
    expect(has(w, 'scripts/run.py')).toBe(false)
  })

  it('展开全部 / 折叠全部', async () => {
    const w = mountTree()
    await w.find('[data-test="expand-all"]').trigger('click')
    expect(has(w, 'scripts/lib/util.py')).toBe(true)
    expect(has(w, 'templates/a.md')).toBe(true)
    await w.find('[data-test="collapse-all"]').trigger('click')
    expect(has(w, 'scripts/run.py')).toBe(false)
    expect(has(w, 'SKILL.md')).toBe(true)
  })

  it('搜索过滤并自动展开命中路径', async () => {
    const w = mountTree()
    // EP 2.13：el-input 的 attrs 合并到内部 input，data-test 直接落在 input 上
    await w.find('[data-test="file-search"]').setValue('util')
    expect(has(w, 'scripts/lib/util.py')).toBe(true)   // 祖先自动展开
    expect(has(w, 'scripts/run.py')).toBe(false)       // 未命中
    expect(dirRow(w, 'templates').exists()).toBe(false)
    await w.find('[data-test="file-search"]').setValue('zzz')
    expect(w.text()).toContain('没有匹配的文件')
  })

  it('点击文件行 emit select；actions 插槽按文件渲染', async () => {
    const w = mount(SkillFilesTree, {
      props: { files: FILES },
      slots: { actions: '<button class="act" data-test="act">编辑</button>' },
    })
    await row(w, 'SKILL.md').trigger('click')
    const events = w.emitted('select')
    expect(events).toBeTruthy()
    expect((events![0][0] as { path: string }).path).toBe('SKILL.md')
    expect(w.find('[data-test="act"]').exists()).toBe(true)
  })

  it('revealPath 展开到目标文件所在目录', async () => {
    const w = mountTree()
    ;(w.vm as unknown as { revealPath: (p: string) => void })
      .revealPath('scripts/lib/util.py')
    await w.vm.$nextTick()
    expect(has(w, 'scripts/lib/util.py')).toBe(true)
  })

  it('大小与时间列渲染（时间缺省显示 —）', async () => {
    const w = mountTree()
    expect(w.text()).toContain('100 B')          // 根级文件默认可见
    await w.find('[data-test="expand-all"]').trigger('click')
    expect(w.text()).toContain('2.0 KB')          // 展开后子级大小列
    expect(w.text()).toContain('—')               // util.py 的 mtime=null
  })

  it('空文件列表显示无文件', () => {
    const w = mountTree({ files: [] })
    expect(w.text()).toContain('无文件')
  })
})
