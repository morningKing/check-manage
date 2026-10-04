# AI 压力测试（ai-stress spec 落地）

运行（默认 pytest 已排除本目录，需显式 `-m stress`）：

```bash
cd server
python -m pytest tests/stress/test_stack_smoke.py -m stress -q   # 栈冒烟 ~1min
python -m pytest tests/stress/test_capacity_ladder.py -m stress -q   # 容量阶梯 ~30-60min
python -m pytest tests/stress/test_chaos_injection.py -m stress -q   # 混沌（真模型）~20-40min
python -m pytest tests/stress/test_readpath_load.py -m stress -q     # 读路径 ~40min
```

- 专属栈：DB `casemanage_stress`（用完 DROP）、后端 :3092（stub 运行时）、
  混沌 serve :4097（临时 GLOBAL_DIR）。与 dev（3002/4096/3003/5173）零共享。
- 产物：`docs/ai-testing/evidence/stress/*.json`（采样/结论）；
  容量结论用 `python -c "from tests.stress.report import render_capacity_report; ..."` 渲染。
- 前置：Postgres 可建库；`opencode` 在 PATH（混沌层）；:3092/:3098/:4097 空闲。
