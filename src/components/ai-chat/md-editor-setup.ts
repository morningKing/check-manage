/**
 * Register bundled mermaid + echarts instances with md-editor-v3 so the chat
 * renders ```mermaid / ```echarts blocks inline, offline (no CDN), and safely.
 * Imported for its side effect by MarkdownView; config() runs once on load.
 */
import { config } from 'md-editor-v3'
import mermaid from 'mermaid'
import * as echarts from 'echarts'

config({
  editorExtensions: {
    mermaid: { instance: mermaid },
    echarts: {
      instance: echarts,
      // Agent output is only semi-trusted. The md-editor default parses the
      // block with `new Function` (to allow function-valued options) — that is
      // arbitrary code execution. Restrict to pure JSON instead.
      parseOption: (code: string) => JSON.parse(code),
    },
  },
  // Block raw HTML / click handlers inside diagrams.
  mermaidConfig: (base: any) => ({ ...base, securityLevel: 'strict' }),
})
