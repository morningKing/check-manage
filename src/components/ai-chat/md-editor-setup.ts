/**
 * Register bundled mermaid + echarts + katex instances with md-editor-v3 so the
 * chat renders ```mermaid / ```echarts blocks and math inline, offline (no
 * CDN), and safely. Imported for its side effect by MarkdownView; config()
 * runs once on load.
 */
import { config } from 'md-editor-v3'
import mermaid from 'mermaid'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import * as echarts from 'echarts'

config({
  editorExtensions: {
    mermaid: { instance: mermaid },
    // 本地 katex 实例：不注册时 md-editor-v3 会在挂载时自动从 CDN 拉取
    // katex@0.16.33——内网/离线环境拉不到时公式静默降级为纯文本，拉得到时
    // 也引入了对公网的运行时依赖。与 mermaid/echarts 同一离线策略。
    katex: { instance: katex },
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
  // KaTeX 默认 strict='warn'：数学环境里出现中文/非常规字符时每个公式都向
  // 控制台打 "LaTeX-incompatible input and strict mode is set to 'warn'"
  // 刷屏。strict:false 按“尽力渲染”处理，不再告警。
  katexConfig: (base: any) => ({ ...base, strict: false }),
  markdownItPlugins: (plugins) => plugins.map((p) =>
    p.type === 'katex'
      ? {
          ...p,
          options: {
            ...p.options,
            // 去掉单个 `$` 行内定界符（md-editor-v3 默认开启）：正文里相隔
            // 两个 `$`（shell 变量、价格、代码片段）之间的中文会被整段当
            // 成公式吃掉，渲染成乱码——看起来像编码错误。保留 `$$…$$` 与
            // `\(…\)`/`\[…\]`；行内公式请用 `\(…\)` 或 `$$…$$`。
            inlineDelimiters: [
              { open: '$$', close: '$$' },
              { open: '\\(', close: '\\)' },
            ],
          },
        }
      : p),
})
