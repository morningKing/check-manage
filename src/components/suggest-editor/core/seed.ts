import type { Editor } from '@tiptap/core'
import type * as Y from 'yjs'

export const DOC_META_MAP = 'meta'
export const SKIP_INITIAL_ANALYSIS_KEY = 'skipInitialAnalysis'

/**
 * 导入种子只在「确实空」时灌入，并标记引擎首轮只建立分析基线。
 * 返回 true 表示本次确实写入了种子内容。
 */
export function applySeedIfEmpty(editor: Editor, ydoc: Y.Doc, seedHtml: string): boolean {
  if (!seedHtml.trim()) return false
  if (ydoc.getXmlFragment('default').length > 0 || editor.getText().trim().length > 0) return false
  editor.commands.setContent(seedHtml)
  ydoc.getMap(DOC_META_MAP).set(SKIP_INITIAL_ANALYSIS_KEY, true)
  return true
}
