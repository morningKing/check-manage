/**
 * Parse a `question` tool-call part (OpenCode's built-in interactive
 * multi-choice tool) into a read-only card view, so the question panel stays
 * rendered — with the user's selection highlighted — after the turn finishes,
 * instead of collapsing into the generic JSON tool bubble.
 *
 * The questions live in the tool INPUT (same shape as the interactive
 * QuestionCard's QuestionRequest). The answers only survive as a formatted
 * string in the tool RESULT — OpenCode builds it as (verified against the
 * binary, src/tool/question.ts):
 *
 *   const formatted = questions
 *     .map((q, i) => `"${q.question}"="${answer.join(", ") || "Unanswered"}"`)
 *     .join(", ")
 *   output: `User has answered your questions: ${formatted}. …`
 *
 * Multi-select answers are the chosen labels joined with ", "; a custom
 * ("其他") answer is the typed text as one array element. Rejecting (跳过)
 * aborts the tool instead: status 'error' with no output — that maps to the
 * per-question 未作答 state below.
 */
import type { QuestionInfo } from '@/api/aiChat'

export type QuestionPartStatus = 'running' | 'answered' | 'unanswered'

export interface QuestionAnswerView {
  /** option labels chosen for this question, in the option list's own order */
  labels: string[]
  /** the typed answer when it matched no option ("其他"/自定义); else null */
  custom: string | null
  /** raw joined answer text; null = the question was never answered */
  raw: string | null
}

export interface QuestionPartView {
  questions: QuestionInfo[]
  answers: QuestionAnswerView[]
  status: QuestionPartStatus
  durationMs?: number
}

function toQuestions(v: unknown): QuestionInfo[] | null {
  let obj: unknown = v
  if (typeof obj === 'string') {
    try { obj = JSON.parse(obj) } catch { return null }
  }
  const arr = obj && typeof obj === 'object'
    ? (obj as Record<string, unknown>).questions : null
  if (!Array.isArray(arr) || !arr.length) return null
  const out: QuestionInfo[] = []
  for (const q of arr) {
    if (!q || typeof q !== 'object') continue
    const rec = q as Record<string, unknown>
    const question = (rec.question ?? '').toString()
    if (!question) continue
    const options = Array.isArray(rec.options)
      ? rec.options
          .filter((o): o is Record<string, unknown> => !!o && typeof o === 'object')
          .map((o) => ({
            label: (o.label ?? '').toString(),
            description: (o.description ?? '').toString(),
          }))
          .filter((o) => o.label)
      : []
    out.push({
      question,
      header: (rec.header ?? '').toString(),
      options,
      multiple: rec.multiple === true,
      custom: rec.custom !== false,
    })
  }
  return out.length ? out : null
}

/** Extract the `"question"="answer"` pairs from OpenCode's result string. */
function extractPairs(result: string): Map<string, string> {
  const map = new Map<string, string>()
  const re = /"([^"]*)"="([^"]*)"/g
  let m: RegExpExecArray | null
  while ((m = re.exec(result)) !== null) {
    map.set(m[1], m[2])
  }
  return map
}

function answerView(q: QuestionInfo, raw: string | null): QuestionAnswerView {
  if (raw == null || raw === '' || raw === 'Unanswered') {
    return { labels: [], custom: null, raw: null }
  }
  // The joined string is labels-and/or-one-custom-text separated by ", ".
  // Match options by exact segment so a custom answer merely *containing* an
  // option label can't light that option up; leftover segments are the custom
  // text (re-joined, recovering ", " typed inside it).
  const segs = raw.split(', ')
  const labels = q.options.map((o) => o.label).filter((l) => segs.includes(l))
  const rest = segs.filter((s) => !labels.includes(s))
  return { labels, custom: rest.length ? rest.join(', ') : null, raw }
}

/**
 * Returns the read-only card view if `part` is a question tool-call with
 * usable questions, else null (caller falls back to the generic tool bubble).
 * A still-running question maps to status 'running' — the interactive card at
 * the thread's end is the UI for that phase; the component renders a compact
 * "waiting" hint so the in-thread position stays marked without duplicating
 * the full option list.
 */
export function parseQuestionPart(
  part: { name?: string; status?: string; input?: unknown; result?: unknown; durationMs?: number } | null | undefined,
): QuestionPartView | null {
  if (!part?.name || part.name !== 'question') return null
  const questions = toQuestions(part.input) ?? toQuestions(part.result)
  if (!questions) return null
  const status: QuestionPartStatus =
    part.status === 'completed' ? 'answered'
    : part.status === 'error' ? 'unanswered'
    : part.status === 'running' ? 'running'
    : 'answered'
  const result = typeof part.result === 'string' ? part.result : ''
  const pairs = status === 'answered' ? extractPairs(result) : new Map<string, string>()
  return {
    questions,
    answers: questions.map((q) => answerView(q, pairs.get(q.question) ?? null)),
    status,
    durationMs: part.durationMs,
  }
}
