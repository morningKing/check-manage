<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="860px"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @open="load"
  >
    <div class="rules-bar">
      <el-button size="small" type="primary" :disabled="loadFailed" @click="openEdit(null)">
        新建规则
      </el-button>
      <el-button size="small" :disabled="loadFailed" @click="aiVisible = true">
        AI 生成…
      </el-button>
      <el-radio-group v-model="importMode" size="small">
        <el-radio-button value="append" :disabled="loadFailed">追加（去重）</el-radio-button>
        <el-radio-button value="replace">覆盖</el-radio-button>
      </el-radio-group>
      <el-button
        size="small"
        :loading="importing"
        :disabled="loadFailed && importMode !== 'replace'"
        @click="fileInput?.click()"
      >
        导入 CSV…
      </el-button>
      <input
        ref="fileInput"
        type="file"
        accept=".csv,.txt,text/csv"
        style="display: none"
        @change="onFile"
      />
      <a :href="rulesExportUrl(props.scope)"><el-button size="small">导出 CSV</el-button></a>
      <el-button size="small" @click="downloadTemplate">下载模板</el-button>
      <span class="rules-spacer" />
      <el-button size="small" @click="emit('update:visible', false)">关闭</el-button>
    </div>

    <el-alert
      v-if="loadFailed"
      class="rules-alert"
      type="error"
      :closable="false"
      show-icon
      title="规则加载失败"
      description="新建、编辑、删除与保存已禁用；若规则文件损坏，可用「覆盖」模式导入整份替换修复。"
    />

    <el-table
      v-loading="loading"
      :data="rules"
      size="small"
      max-height="420"
      :empty-text="loadFailed ? '规则加载失败' : '暂无规则，点「新建规则」添加'"
    >
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="严重度" width="80">
        <template #default="{ row }">{{ SEVERITY_LABEL[row.severity as LocalCheckRule['severity']] ?? row.severity }}</template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }">{{ row.enabled ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="判定" min-width="220">
        <template #default="{ row }">{{ summary(row) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="loadFailed" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" :disabled="loadFailed" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="editVisible"
      :title="draft.id ? '编辑规则' : '新建规则'"
      width="640px"
      append-to-body
    >
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="draft.name" maxlength="100" placeholder="规则名，如：型号纠错" />
        </el-form-item>
        <el-form-item label="严重度">
          <el-select v-model="draft.severity" style="width: 120px">
            <el-option value="info" label="提示" />
            <el-option value="warn" label="建议" />
            <el-option value="error" label="错误" />
          </el-select>
          <el-switch v-model="draft.enabled" active-text="启用" style="margin-left: 16px" />
        </el-form-item>
        <el-form-item label="AI 说明">
          <el-input
            v-model="draft.aiPrompt"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="可选：注入 AI 分析的语义核对说明"
          />
        </el-form-item>
        <el-divider content-position="left">本地判定（可组合，至少填一项）</el-divider>

        <el-form-item label="错误映射">
          <div class="rule-rows">
            <div v-for="(m, i) in draft.maps" :key="i" class="rule-row">
              <el-input v-model="m.wrong" placeholder="错误写法" style="width: 180px" />
              <span class="rule-arrow">→</span>
              <el-input v-model="m.right" placeholder="正确写法" style="width: 180px" />
              <el-checkbox v-model="m.caseSensitive">区分大小写</el-checkbox>
              <el-button link type="danger" @click="draft.maps.splice(i, 1)">删除</el-button>
            </div>
            <el-button
              size="small"
              @click="draft.maps.push({ wrong: '', right: '', caseSensitive: false })"
            >
              + 映射
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="正则校验">
          <div class="rule-rows">
            <div v-for="(p, i) in draft.patterns" :key="i" class="rule-row">
              <el-input v-model="p.extract" placeholder="提取正则" style="width: 170px" />
              <el-input v-model="p.valid" placeholder="校验正则" style="width: 170px" />
              <el-input
                v-model="p.message"
                data-field="message"
                placeholder="不匹配提示"
                style="width: 150px"
              />
              <el-button link type="danger" @click="draft.patterns.splice(i, 1)">删除</el-button>
            </div>
            <el-button
              size="small"
              @click="draft.patterns.push({ extract: '', valid: '', message: '', flags: '' })"
            >
              + 正则
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="锚定白名单">
          <div class="rule-rows">
            <div v-for="(w, i) in draft.whitelists" :key="i" class="rule-card">
              <div class="rule-row">
                <el-input
                  v-model="w.anchors"
                  data-field="anchors"
                  type="textarea"
                  :rows="2"
                  placeholder="锚点，一行一个（如：型号：）"
                  style="width: 180px"
                />
                <el-input
                  v-model="w.values"
                  data-field="values"
                  type="textarea"
                  :rows="2"
                  placeholder="允许取值，一行一个"
                  style="width: 180px"
                />
                <el-input
                  v-model="w.message"
                  data-field="message"
                  placeholder="不在白名单时的提示"
                  style="width: 150px"
                />
                <el-button link type="danger" @click="draft.whitelists.splice(i, 1)">删除</el-button>
              </div>
              <div class="rule-row">
                <el-input
                  v-model="w.valuePattern"
                  placeholder="取值正则（可选，默认 [A-Za-z0-9_\-./]+）"
                  style="width: 300px"
                />
              </div>
            </div>
            <el-button
              size="small"
              @click="draft.whitelists.push({ anchors: '', values: '', valuePattern: '', message: '' })"
            >
              + 白名单
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="条件完整性">
          <div class="rule-rows">
            <div v-for="(p, i) in draft.presence" :key="i" class="rule-card">
              <div class="rule-row">
                <el-input
                  v-model="p.triggers"
                  data-field="triggers"
                  type="textarea"
                  :rows="2"
                  placeholder="触发词，一行一个（如：9800M12）"
                  style="width: 180px"
                />
                <el-input
                  v-model="p.requiresAny"
                  data-field="requiresAny"
                  type="textarea"
                  :rows="2"
                  placeholder="必须出现其一，一行一个"
                  style="width: 180px"
                />
                <el-input
                  v-model="p.message"
                  data-field="message"
                  placeholder="缺失时的提示"
                  style="width: 150px"
                />
                <el-button link type="danger" @click="draft.presence.splice(i, 1)">删除</el-button>
              </div>
            </div>
            <el-button
              size="small"
              @click="draft.presence.push({ triggers: '', requiresAny: '', message: '' })"
            >
              + 完整性
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="loadFailed" @click="saveDraft">
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 自然语言 → 规则：AI 生成草稿，核对后随整份规则保存 -->
    <el-dialog v-model="aiVisible" title="用自然语言生成规则" width="640px" append-to-body>
      <el-input
        v-model="aiText"
        type="textarea"
        :rows="4"
        data-field="nl-rule"
        placeholder="例如：涉及 9800M12 时必须写 9800UPS 或声明不涉及拖带；型号 S-5700 应写作 S5700"
      />
      <div class="rules-ai-hint">
        AI 会把它转成结构化规则（映射 / 正则 / 白名单 / 完整性 / AI 说明），生成后请核对再保存。约 10–60 秒。
      </div>
      <template #footer>
        <el-button @click="aiVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="aiGenerating"
          :disabled="!aiText.trim()"
          @click="generateByAi"
        >
          生成
        </el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 检查规则管理对话框：全局（admin）/文档级（属主）共用。
 * 表格管理规则，内嵌动态表单编辑「映射 / 正则 / 白名单 / 完整性」四种判定；
 * 保存走整份替换（saveRules），导入成功后同样通知宿主动态重载规则。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  convertRules,
  getRules,
  importRules,
  rulesExportUrl,
  saveRules,
  type LocalCheckRule,
  type VocabularyScope,
} from '@/api/suggestEditor'

const props = defineProps<{ visible: boolean; scope: VocabularyScope }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void; (e: 'saved'): void }>()

type Severity = LocalCheckRule['severity']

interface MapDraft {
  wrong: string
  right: string
  caseSensitive: boolean
}

interface PatternDraft {
  extract: string
  valid: string
  message: string
  flags: string
}

interface WhitelistDraft {
  anchors: string
  values: string
  valuePattern: string
  message: string
}

interface PresenceDraft {
  triggers: string
  requiresAny: string
  message: string
}

interface RuleDraft {
  id: string
  name: string
  enabled: boolean
  severity: Severity
  aiPrompt: string
  maps: MapDraft[]
  patterns: PatternDraft[]
  whitelists: WhitelistDraft[]
  presence: PresenceDraft[]
}

const SEVERITY_LABEL: Record<Severity, string> = { info: '提示', warn: '建议', error: '错误' }

const rules = ref<LocalCheckRule[]>([])
const loading = ref(false)
/** 加载失败时禁止一切写操作：避免空表 + 整份 PUT 覆盖服务端全部规则 */
const loadFailed = ref(false)
const editVisible = ref(false)
const draft = ref<RuleDraft>(blankDraft())
const saving = ref(false)
const importing = ref(false)
const importMode = ref<'append' | 'replace'>('append')
const fileInput = ref<HTMLInputElement | null>(null)
const aiVisible = ref(false)
const aiText = ref('')
const aiGenerating = ref(false)

const title = computed(() =>
  props.scope.kind === 'global'
    ? '检查规则（全局，对所有文档生效）'
    : '检查规则（仅本文档生效）',
)

function blankDraft(): RuleDraft {
  return {
    id: '',
    name: '',
    enabled: true,
    severity: 'warn',
    aiPrompt: '',
    maps: [],
    patterns: [],
    whitelists: [],
    presence: [],
  }
}

function newRuleId(): string {
  // crypto.randomUUID 仅 secure context 可用（http 内网地址下为 undefined）：
  // 兜底用时间戳 + 随机串，最终仍形如 rule-<token>（服务端 id ≤64 字符）。
  const uuid = globalThis.crypto?.randomUUID?.()
  const token = uuid
    ? uuid.slice(0, 12)
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
  return `rule-${token.replace(/[^a-zA-Z0-9-]/g, '')}`
}

function toDraft(rule: LocalCheckRule): RuleDraft {
  return {
    id: rule.id,
    name: rule.name,
    enabled: rule.enabled,
    severity: rule.severity,
    aiPrompt: rule.aiPrompt ?? '',
    maps: (rule.maps ?? []).map((m) => ({
      wrong: m.wrong,
      right: m.right,
      caseSensitive: m.caseSensitive ?? false,
    })),
    patterns: (rule.patterns ?? []).map((p) => ({
      extract: p.extract,
      valid: p.valid,
      message: p.message,
      flags: p.flags ?? '',
    })),
    whitelists: (rule.whitelists ?? []).map((w) => ({
      anchors: w.anchors.join('\n'),
      values: w.values.join('\n'),
      valuePattern: w.valuePattern ?? '',
      message: w.message,
    })),
    presence: (rule.presence ?? []).map((p) => ({
      triggers: p.triggers.join('\n'),
      requiresAny: p.requiresAny.join('\n'),
      message: p.message,
    })),
  }
}

function summary(rule: LocalCheckRule): string {
  const parts: string[] = []
  if (rule.maps?.length) parts.push(`映射 ${rule.maps.length}`)
  if (rule.patterns?.length) parts.push(`正则 ${rule.patterns.length}`)
  if (rule.whitelists?.length) parts.push(`白名单 ${rule.whitelists.length}`)
  if (rule.presence?.length) parts.push(`完整性 ${rule.presence.length}`)
  if (rule.aiPrompt) parts.push('AI')
  return parts.join(' · ') || '—'
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rules.value = (await getRules(props.scope)).rules
    loadFailed.value = false
  } catch {
    // 保留空表但标记失败：任何写操作（含整份 PUT）都被禁止，直到重新加载成功
    rules.value = []
    loadFailed.value = true
    ElMessage.error('规则加载失败，暂不能保存以避免覆盖服务端规则')
  } finally {
    loading.value = false
  }
}

function openEdit(rule: LocalCheckRule | null): void {
  if (loadFailed.value) {
    ElMessage.warning('规则加载失败，暂不能编辑规则')
    return
  }
  draft.value = rule ? toDraft(rule) : blankDraft()
  editVisible.value = true
}

/** 多值字段：一行一个值（trim、去空行） */
function splitLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function isBlank(...values: string[]): boolean {
  return values.every((value) => !value.trim())
}

function regexOk(source: string, flags?: string): boolean {
  try {
    new RegExp(source, flags)
    return true
  } catch {
    return false
  }
}

function cleanDraft(source: RuleDraft): LocalCheckRule | null {
  const name = source.name.trim()
  if (!name) {
    ElMessage.warning('规则名称不能为空')
    return null
  }

  /* ------------------------------- 映射 ------------------------------- */
  const maps: NonNullable<LocalCheckRule['maps']> = []
  for (const m of source.maps) {
    if (isBlank(m.wrong, m.right)) continue
    const wrong = m.wrong.trim()
    const right = m.right.trim()
    if (!wrong) {
      ElMessage.warning('错误映射的「错误写法」不能为空')
      return null
    }
    if (!right) {
      ElMessage.warning('错误映射的「正确写法」不能为空')
      return null
    }
    maps.push({ wrong, right, caseSensitive: m.caseSensitive })
  }

  /* ------------------------------- 正则 ------------------------------- */
  const patterns: NonNullable<LocalCheckRule['patterns']> = []
  for (const p of source.patterns) {
    if (isBlank(p.extract, p.valid, p.message, p.flags)) continue
    const extract = p.extract.trim()
    const valid = p.valid.trim()
    const message = p.message.trim()
    const flags = p.flags.trim()
    if (!extract) {
      ElMessage.warning('正则校验的「提取正则」不能为空')
      return null
    }
    if (!valid) {
      ElMessage.warning('正则校验的「校验正则」不能为空')
      return null
    }
    if (!message) {
      ElMessage.warning('正则校验的「提示语」不能为空')
      return null
    }
    if (!regexOk(extract)) {
      ElMessage.warning(`提取正则无法编译：${extract}`)
      return null
    }
    if (!regexOk(valid)) {
      ElMessage.warning(`校验正则无法编译：${valid}`)
      return null
    }
    if (flags && !regexOk(extract, flags.replace(/[gy]/g, '') + 'g')) {
      ElMessage.warning(`正则标志无法编译：${flags}`)
      return null
    }
    patterns.push({ extract, valid, message, ...(flags ? { flags } : {}) })
  }

  /* ----------------------------- 白名单 ----------------------------- */
  const whitelists: NonNullable<LocalCheckRule['whitelists']> = []
  for (const w of source.whitelists) {
    if (isBlank(w.anchors, w.values, w.valuePattern, w.message)) continue
    const anchors = splitLines(w.anchors)
    const values = splitLines(w.values)
    const valuePattern = w.valuePattern.trim()
    const message = w.message.trim()
    if (!anchors.length) {
      ElMessage.warning('锚定白名单的「锚点」不能为空（一行一个）')
      return null
    }
    if (!values.length) {
      ElMessage.warning('锚定白名单的「允许取值」不能为空（一行一个）')
      return null
    }
    if (!message) {
      ElMessage.warning('锚定白名单的「提示语」不能为空')
      return null
    }
    if (valuePattern && !regexOk(valuePattern)) {
      ElMessage.warning(`白名单取值正则无法编译：${valuePattern}`)
      return null
    }
    whitelists.push({ anchors, values, message, ...(valuePattern ? { valuePattern } : {}) })
  }

  /* ----------------------------- 完整性 ----------------------------- */
  const presence: NonNullable<LocalCheckRule['presence']> = []
  for (const p of source.presence) {
    if (isBlank(p.triggers, p.requiresAny, p.message)) continue
    const triggers = splitLines(p.triggers)
    const requiresAny = splitLines(p.requiresAny)
    const message = p.message.trim()
    if (!triggers.length) {
      ElMessage.warning('条件完整性的「触发词」不能为空（一行一个）')
      return null
    }
    if (!requiresAny.length) {
      ElMessage.warning('条件完整性的「必须词」不能为空（一行一个）')
      return null
    }
    if (!message) {
      ElMessage.warning('条件完整性的「提示语」不能为空')
      return null
    }
    presence.push({ triggers, requiresAny, message })
  }

  const aiPrompt = source.aiPrompt.trim()
  if (!maps.length && !patterns.length && !whitelists.length && !presence.length && !aiPrompt) {
    ElMessage.warning('至少填写一种本地判定或 AI 说明')
    return null
  }

  return {
    id: source.id || newRuleId(),
    name,
    enabled: source.enabled,
    severity: source.severity,
    ...(aiPrompt ? { aiPrompt } : {}),
    ...(maps.length ? { maps } : {}),
    ...(patterns.length ? { patterns } : {}),
    ...(whitelists.length ? { whitelists } : {}),
    ...(presence.length ? { presence } : {}),
  }
}

function saveDraft(): void {
  if (loadFailed.value) {
    ElMessage.warning('规则加载失败，暂不能保存以避免覆盖服务端规则')
    return
  }
  const cleaned = cleanDraft(draft.value)
  if (!cleaned) return
  const index = rules.value.findIndex((r) => r.id === cleaned.id)
  if (index >= 0) rules.value.splice(index, 1, cleaned)
  else rules.value.push(cleaned)
  editVisible.value = false
  void persist()
}

async function persist(): Promise<void> {
  if (loadFailed.value) {
    ElMessage.warning('规则加载失败，暂不能保存以避免覆盖服务端规则')
    return
  }
  saving.value = true
  try {
    await saveRules(props.scope, rules.value)
    ElMessage.success('规则已保存')
    emit('saved')
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    saving.value = false
  }
}

/** 自然语言 → 规则：AI 返回已校验的草稿，合入列表后整份保存 */
async function generateByAi(): Promise<void> {
  if (loadFailed.value) {
    ElMessage.warning('规则加载失败，暂不能生成/保存')
    return
  }
  const description = aiText.value.trim()
  if (!description) return
  aiGenerating.value = true
  try {
    const { rules: drafts } = await convertRules(description)
    for (const generated of drafts) {
      const index = rules.value.findIndex((rule) => rule.id === generated.id)
      if (index >= 0) rules.value.splice(index, 1, generated)
      else rules.value.push(generated)
    }
    aiVisible.value = false
    aiText.value = ''
    await persist()
    ElMessage.success(`AI 已生成 ${drafts.length} 条规则并保存，请在列表中核对`)
  } catch (error) {
    const message = (error as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(message || 'AI 生成失败，请稍后重试')
  } finally {
    aiGenerating.value = false
  }
}

async function remove(rule: LocalCheckRule): Promise<void> {
  if (loadFailed.value) {
    ElMessage.warning('规则加载失败，暂不能删除规则')
    return
  }
  rules.value = rules.value.filter((r) => r.id !== rule.id)
  await persist()
}

async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (loadFailed.value && importMode.value !== 'replace') {
    ElMessage.warning('规则加载失败时仅支持「覆盖」导入')
    return
  }
  if (!file) return
  if (loadFailed.value) {
    // 规则文件损坏/加载失败时覆盖会丢失服务端现有规则，二次确认
    try {
      await ElMessageBox.confirm(
        '规则文件损坏/加载失败时，覆盖导入将替换服务端全部规则，确定继续？',
        '覆盖导入',
        { type: 'warning', confirmButtonText: '覆盖导入', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  importing.value = true
  try {
    const r = await importRules(props.scope, file, importMode.value)
    ElMessage.success(`导入完成：新增 ${r.added}，跳过 ${r.skipped}，共 ${r.total} 条`)
    await load()
    emit('saved')
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    importing.value = false
  }
}

function downloadTemplate(): void {
  const csv =
    'type,name,severity,enabled,wrong,right,extract,valid,anchors,values,triggers,requiresAny,message,aiPrompt\n'
    + 'map,型号纠错,error,true,S-5700,S5700,,,,,,,,\n'
    + 'presence,拖带完整性,warn,true,,,,,,,9800M12,9800UPS|不涉及拖带,缺拖带说明,\n'
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = '规则模板.csv'
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped lang="scss">
.rules-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.rules-spacer {
  flex: 1;
}

.rules-ai-hint {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.rules-alert {
  margin-bottom: 12px;
}

.rule-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.rule-row {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 8px;
}

.rule-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px;
  border: 1px dashed var(--el-border-color-lighter);
  border-radius: 6px;
}

.rule-arrow {
  line-height: 32px;
  color: var(--el-text-color-secondary);
}
</style>
