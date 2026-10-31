# image-parts-spike — OpenCode 图片 part 透传探测（Feature C 闸门）

2026-10-30 第一档计划 Task 8：对 pinned oc serve（1.26，`http://127.0.0.1:4096`）
实测 `POST /session/{id}/prompt_async` 的 parts 是否接受图片 part，并判定模型是否
真的"看到"图。**两种形态都失败 → Feature C（Task 9/10）整体砍掉**（用户已确认）。

## 两种候选形态

1. `dataurl` —— `{"type":"file","mime":"image/png","url":"data:image/png;base64,..."}`
2. `filepath` —— `{"type":"file","mime":"image/png","url":"file://<绝对路径>"}`

## 运行方法

```bash
# 探测图（1×1 红色 PNG）
python -c "import base64,sys; open('e2e/manual/image-parts-spike/probe.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"

# token 取 oc-serve 启动日志的 gate token 行（fingerprint 前缀对上 server/.env 的
# MCP_INTERNAL_TOKEN）；--workspace 用任一真实会话工作区绝对路径（本次用
# .dev-ai-workspaces/user-admin/sess_9e3042e1075a，filepath 模式先把 probe.png 拷进去）
python e2e/manual/image-parts-spike/spike.py \
  --base-url http://127.0.0.1:4096 --token <token> \
  --model opencode-go/deepseek-v4.1-flash \
  --workspace <会话工作区绝对路径> \
  --image <probe.png 绝对路径> --mode dataurl    # 或 filepath

# 退出码 0=该形态可用；2=不可用。结果 JSON 落 docs/ai-testing/evidence/。
```

模型选择：`/provider` 返回清单里按 `capabilities.input.image == true` 过滤；
选平台默认模型 `opencode-go/deepseek-v4.1-flash`（`image:true`、active、平台已接好
OPENCODE_API_KEY，与 server/.env 的 AGENT_MODEL 同款）。

## 与 brief 脚本的一处偏差（重要）

brief 交付的 spike.py 从 `info.get('parts')` 读消息 parts，但 oc serve 1.26 的
`GET /session/{id}/message` 信封是 `{info: {...}, parts: [...]}`——**parts 在消息
顶层、info 内没有 parts 键**，原脚本任何情况下都读不到 assistant 回复（首轮实测
`assistant_texts: []`、退出码 2，即 evidence `...103928-dataurl.json`，作废留档）。
已做最小修复：`m.get('parts') or info.get('parts')` 两种信封都兼容，其余逻辑逐字
保持 brief 原样。模型层面首轮实际已看到图（会话 `ses_e702e4abeffexVYgu2oNrXPVLJ`
回复"纯红色方块"，reasoning 明确描述红色方块），与修复后结论一致。

## 实测结果（2026-10-31，oc serve 1.26）

| 形态 | 退出码 | 模型回复 | evidence |
|------|--------|----------|----------|
| dataurl  | 0 | 红色圆形 | `docs/ai-testing/evidence/image-parts-spike-20261031-104211-dataurl.json` |
| filepath | 0 | 纯红色图片 | `docs/ai-testing/evidence/image-parts-spike-20261031-104237-filepath.json` |
| dataurl（修复前脚本，作废） | 2 | （脚本信封 bug 未读到） | `docs/ai-testing/evidence/image-parts-spike-20261031-103928-dataurl.json` |

## 结论

**两种形态均通过 → Feature C 保留。`IMAGE_PART_MODE = dataurl`**（两种皆可用，
取 dataurl：自包含、无文件系统路径耦合，用户上传图无需落入 oc serve 可读目录；
filepath 可作 fallback）。判据：回复含"红"（brief Step 5 的 pass criterion）。
