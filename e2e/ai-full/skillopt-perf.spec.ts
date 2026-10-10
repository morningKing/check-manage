/**
 * SkillOpt 任务性能分析（spec 2026-10-09）。
 * 用例 1 纯 DB 播种（零 LLM）：播种 attempt+manifests+消息+子代理 →
 * 性能 tab 渲染定义/趋势/下钻覆盖条与诊断列表。
 * 用例 2 真 LLM 批链路：建批跑终态后同一页面断言真实任务出现（@llm）。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth, tag, screenshot } from './helpers'
import {
  adminToken, createBatch, uploadStaging, waitBatchTerminal, cleanupBatch,
} from './batch/batch-helpers'
import { execFileSync } from 'node:child_process'

// SkillOpt 页挂在 settingsCatalog 条目 id 'ai-skills'（/admin/ai-skills，页内
// ElTabs 的「性能分析」pane）；'ai-execution' 的 tab 集合是
// batches/sessions/orchestrations，?tab=skillopt 会被 SettingsTabShell.normalize
// 回退到首个 tab（批量执行）——所以这里用真实路由 /admin/ai-skills。
const PAGE = '/admin/ai-skills'

function dbExec(sql: string) {
  execFileSync('python', ['-c', `
import sys; sys.path.insert(0, 'server')
from db import get_db
with get_db() as conn:
    with conn.cursor() as cur:
        cur.execute("""${sql}""")
    conn.commit()
`], { cwd: 'E:/wsl/check/check-manage', encoding: 'utf-8' })
}

// 确定性种子的固定 id（便于清理；复跑前先清防撞）。
// agent_tool_calls 对 sessions 无 FK——会话级联清不掉工具调用行，
// 而 (oc_session_id, part_id) 唯一索引使复跑必撞，故必须显式先清。
const PF_CLEANUP = `
  DELETE FROM agent_tool_calls WHERE oc_session_id = 'oc-pf-e2e';
  DELETE FROM ai_chat_sessions WHERE id = 'pf-e2e-sess';
  DELETE FROM ai_execution_attempts WHERE id = 'pf-e2e-att';
  DELETE FROM ai_chat_batches WHERE id = 'pf-e2e-batch';
  DELETE FROM users WHERE id = 'pf-e2e-user';
`

test('性能分析：DB 播种任务在视图中渲染（趋势+下钻+诊断）', async ({ page }) => {
  const mark = tag('perf')
  // 复跑防撞：先清可能的历史残留（上一次中断的 finally 未执行到）
  dbExec(PF_CLEANUP)
  dbExec(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('pf-e2e-user', 'pf_e2e', 'x', 'PF E2E', 'developer')
    ON CONFLICT (id) DO NOTHING;
  `)
  // 会话/批/attempt/manifests/消息/子代理（一次 SQL 搞定，id 用固定前缀便于清理）
  dbExec(`
    INSERT INTO ai_chat_batches (id, user_id, name, prompt, total)
    VALUES ('pf-e2e-batch', 'pf-e2e-user', '${mark}', 'p', 1);
    INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq, opencode_session_id, workspace_path)
    VALUES ('pf-e2e-sess', 'pf-e2e-user', 'completed', 'pf-e2e-batch', 0, 'oc-pf-e2e', 'C:/tmp/pf-e2e');
    INSERT INTO ai_execution_attempts (id, session_id, source_type, source_id, status, started_at, finished_at)
    VALUES ('pf-e2e-att', 'pf-e2e-sess', 'batch', 'pf-e2e-batch', 'completed',
            NOW() - interval '500 seconds', NOW() - interval '100 seconds');
    -- id/source 为 NOT NULL 无默认，brief 版 INSERT 缺这两列会直接违约
    INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source, path, content_hash, injected)
    VALUES ('pf-e2e-man', 'pf-e2e-att', 'skill', 'perf-e2e-skill', 'platform_global',
            'C:/tmp/pf-e2e/SKILL.md', 'h', true);
    -- 消息 created_at 与 attempt started_at 重合：聚合时窗用 >= 含端点，
    -- 边界消息计入模型区间（确定性切分：模型 200s/等待 100s/间隙 100s）
    INSERT INTO ai_chat_messages (id, session_id, role, content, meta, created_at)
    VALUES ('pf-e2e-msg', 'pf-e2e-sess', 'assistant', '[{"type":"text","text":"done"}]'::jsonb,
            '{"durationMs":200000,"tokensInput":50000,"tokensOutput":3000}'::jsonb,
            NOW() - interval '500 seconds');
    INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description, status, created_at, completed_at)
    VALUES ('ses-pf-e2e', 'pf-e2e-sess', 'general', 'e2e 委派', 'completed',
            NOW() - interval '400 seconds', NOW() - interval '200 seconds');
    -- 工具调用两行（attempt 时窗内；不显式插 id——BIGSERIAL 自增）：
    -- bash 150s + 50s = 200s 工具时长，聚合表应出现 bash 且从模型活跃中切出
    INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id,
      tool, args_text, state, occurred_at, started_at, duration_ms) VALUES
    ('oc-pf-e2e', 'pf-e2e-sess', 'pt1', 'bash', '{}', 'completed',
     NOW() - interval '350 seconds', NOW() - interval '350 seconds', 150000),
    ('oc-pf-e2e', 'pf-e2e-sess', 'pt2', 'bash', '{}', 'completed',
     NOW() - interval '300 seconds', NOW() - interval '300 seconds', 50000);
  `)
  try {
    await gotoWithAuth(page, PAGE)
    await page.getByText('性能分析').click()
    // 定义清单出现播种定义并点入（scope 到 .perf-def-item，避免与慢任务 Top
    // 的 defName 文本重名时 strict mode 撞车）
    const defItem = page.locator('.perf-def-item').filter({ hasText: 'perf-e2e-skill' })
    await expect(defItem).toBeVisible({ timeout: 15_000 })
    await defItem.click()
    await expect(page.locator('[data-test="perf-trend"]')).toBeVisible({ timeout: 10_000 })
    // 任务表首行（ElTable row-click 打开下钻；表列无 sessionId，按行点）
    await page.locator('.perf-view .el-table__row').first().click()
    await expect(page.locator('[data-test="cov-model"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-test="diag-list"]')).toBeVisible()
    // 工具耗时聚合表：播种的两条 bash 工具调用带真实时长 → 表可见且含 bash
    await expect(page.locator('[data-test="tool-perf-table"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-test="tool-perf-table"]')).toContainText('bash')
    await screenshot(page, 'skillopt-perf-seeded-drilldown')
  } finally {
    dbExec(PF_CLEANUP)
  }
})

test('性能分析：真实批任务收敛后出现在性能视图（@llm）', async ({ page }) => {
  test.info().annotations.push({ type: 'llm' })
  test.setTimeout(420_000)
  const tk = await adminToken()
  const name = tag('perf-llm')
  const staged = await uploadStaging(tk, 'in.txt', 'hello\n', `e2e-perf-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '读取 uploads/in.txt 并复述其内容，一句话即可。',
    files: [staged],
  })
  const bid = created.batchId || created.batch?.id
  try {
    await waitBatchTerminal(tk, bid, 360_000)
    await gotoWithAuth(page, PAGE)
    await page.getByText('性能分析').click()
    // 该批默认 agent 在 workspace 无 skill/agent 定义文件 → manifest 只有
    // guidance，慢任务 Top 行的归属显示回退为 sessionId（不含批名）——
    // 断言 Top 非空并点首行打开下钻（真链路的视图可用性）
    await expect(page.locator('.perf-slow-top .row').first())
      .toBeVisible({ timeout: 10_000 })
    await page.locator('.perf-slow-top .row').first().click()
    await expect(page.locator('[data-test="cov-bar"]')).toBeVisible({ timeout: 10_000 })
    await screenshot(page, 'skillopt-perf-llm-drilldown')
  } finally {
    await cleanupBatch(tk, bid)
  }
})
