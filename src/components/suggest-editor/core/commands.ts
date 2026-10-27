import type { Editor } from "@tiptap/core"
import * as Y from "yjs"
import { absolutePositionToRelativePosition, ySyncPluginKey } from "@tiptap/y-tiptap"

export const COMMANDS_MAP = "commands"

/**
 * 写入一条「改写」命令（选中文本 → 右键菜单 → 一键或一句话指令）。
 * 服务端监听到 pending 命令后调用 agent，结果以建议卡片的形式回到文档里，
 * 由用户点「接受」才真正落到正文。
 */
export function requestRewrite(input: {
  ydoc: Y.Doc
  editor: Editor
  instruction: string
  from: number
  to: number
}): string | null {
  const { ydoc, editor, instruction, from, to } = input
  const state = editor.state
  const sync = ySyncPluginKey.getState(state)
  if (!sync?.binding) return null

  const quote = state.doc.textBetween(from, to, "\n")
  if (!quote.trim()) return null

  const relFrom = Y.encodeRelativePosition(
    absolutePositionToRelativePosition(from, sync.type, sync.binding.mapping),
  )
  const relTo = Y.encodeRelativePosition(absolutePositionToRelativePosition(to, sync.type, sync.binding.mapping))

  const id = crypto.randomUUID()
  ydoc.getMap(COMMANDS_MAP).set(id, {
    id,
    kind: "rewrite",
    instruction,
    quote,
    relFrom,
    relTo,
    status: "pending",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  })
  return id
}

/** 写入一条「续写联想」命令（ghost text）；结果只回给发起它的客户端。
 *  mode="paragraph" 是用户主动发起的整段续写（服务端放宽字数与提示词）。 */
export function requestCompletion(
  ydoc: Y.Doc,
  clientId: number,
  context: string,
  mode?: "paragraph",
): string {
  const id = crypto.randomUUID()
  ydoc.getMap(COMMANDS_MAP).set(id, {
    id,
    kind: "complete",
    clientId,
    context,
    ...(mode ? { mode } : {}),
    status: "pending",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  })
  return id
}
