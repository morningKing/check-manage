# Mimosa 完整扫描记录（2026-10-06，合并 capability-stress 后）

- **Scan**: `scan-2026-10-05T23-12-05.869Z-8cee79fecbaf`（deep）
- **Seal**: `sha256:56d160f105cf194201ec41a7e2c0623727a0a041d4a776fbd3de99faa6cde45a`
- **总量**: 151 findings（high 120 / medium 15 / low 16），909 依赖包，离线告警命中 17 包/54 条
- **运行状态**: inconclusive（覆盖缺口：源清单收集不完整、部分分析阶段未完整覆盖）——结论按「静态启发式清单」对待，**不构成安全保证**
- 上轮对照：`scan-2026-10-05T04-32-10.493Z-c51255f21743`（150，high 119）

## 相对上轮的差异（identity 级 diff + ±25 行漂移配对）

| 差异 | 定性 |
|---|---|
| `server/utils/runtime/stub.py` :69→:79/:80/:82 不安全随机数 ×3 | 行漂移（压测 stub 的延迟抖动 `random.uniform`，测试基础设施非安全面，误报） |
| `server/utils/batch_engine.py` :954→:966 SQL 注入 high | 行漂移（本分支 `_resolve_max_concurrent` 插入 18 行所致；存量项，参数化 SQL 惯例的已知误报族） |
| `server/routes/ai_session_admin.py` :84→:86 疑似跨文件污点 medium | 行漂移（存量项） |
| **`server/routes/ai_session_admin.py:1261` 路径穿越 high** | **本条为上轮覆盖缺口漏扫的存量项（该文件本分支未改动）**。triage：SkillOpt 定义回滚端点，`open(path)` 前有 `_path_in_allowed_roots` 守卫（realpath 归一 + commonpath 包含 + AI 工作区/全局技能双白名单根 + 跨盘符视为逃逸，SkillOpt 终审 Fix 3）且端点有 admin 权限门——**判定误报** |

**净结论：无本批改动引入的新增安全问题；唯一真新增是上轮漏扫的存量项且已 triage 为误报。**

## 存量基线（未变）

high 120 中绝大多数为此前已知族：脚本类 `exec`/`eval`（script_runner/etl_engine/debug_export——产品设计即"执行用户脚本"，沙箱边界是另一命题）、入口类 SSRF/SQL 拼接启发式（项目惯例参数化 SQL，误报率高）、`src/utils/storage.ts` AUTH_TOKEN（前端公开常量，jwt-secret-validation 分支已处理过服务端默认密钥问题）。**151 条的系统性 triage 仍是独立任务**（沿 2026-10-05 记录口径）。
