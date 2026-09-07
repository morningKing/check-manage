<script setup lang="ts">
import { ElIcon, ElTag } from 'element-plus'
import { ChatDotRound, Check } from '@element-plus/icons-vue'
import type { QuestionPartView } from '@/utils/questionPart'

// Read-only twin of the interactive QuestionCard: renders a completed (or
// aborted) `question` tool-call as the same option-card layout, with the
// user's choices highlighted, so the panel stays readable in history instead
// of collapsing into the generic JSON tool bubble. While the question is
// still pending (status 'running') only a compact hint is shown — the live
// interactive card at the thread's end is the UI for that phase.
defineProps<{ view: QuestionPartView }>()

function isPicked(view: QuestionPartView, qi: number, label: string): boolean {
  return view.answers[qi]?.labels.includes(label) ?? false
}
</script>

<template>
  <div class="qrcard">
    <div class="qrcard__head">
      <ElIcon class="qrcard__icon"><ChatDotRound /></ElIcon>
      <span>AI 的提问</span>
      <ElTag v-if="view.status === 'answered'" size="small" type="success">已回答</ElTag>
      <ElTag v-else-if="view.status === 'running'" size="small">等待选择</ElTag>
      <ElTag v-else size="small" type="info">未作答</ElTag>
      <span v-if="view.durationMs" class="qrcard__dur">{{ (view.durationMs / 1000).toFixed(1) }}s</span>
    </div>

    <div v-if="view.status === 'running'" class="qrcard__hint">
      AI 正在等待你的选择（见下方选项卡片）…
    </div>

    <template v-else>
      <div v-for="(q, qi) in view.questions" :key="qi" class="qr-block">
        <div class="qr-block__header">{{ q.header }}</div>
        <div class="qr-block__text">{{ q.question }}</div>

        <div class="qr-options">
          <div
            v-for="opt in q.options" :key="opt.label"
            class="qr-option" :class="{ 'qr-option--checked': isPicked(view, qi, opt.label) }"
          >
            <span class="qr-option__mark">
              <ElIcon v-if="isPicked(view, qi, opt.label)"><Check /></ElIcon>
            </span>
            <span class="qr-option__body">
              <span class="qr-option__label">{{ opt.label }}</span>
              <span v-if="opt.description" class="qr-option__desc">{{ opt.description }}</span>
            </span>
          </div>

          <!-- 自定义（"其他"）回答：不在选项里的那段答案文本 -->
          <div v-if="view.answers[qi]?.custom" class="qr-option qr-option--checked">
            <span class="qr-option__mark"><ElIcon><Check /></ElIcon></span>
            <span class="qr-option__body">
              <span class="qr-option__label">其他：{{ view.answers[qi]?.custom }}</span>
            </span>
          </div>

          <div v-if="!view.answers[qi]?.raw" class="qr-option__unanswered">未作答</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.qrcard {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  padding: 12px 14px;
  background: var(--el-fill-color-lighter);
}
.qrcard__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 10px;
}
.qrcard__icon { color: var(--el-color-primary); }
.qrcard__dur {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
.qrcard__hint { font-size: 13px; color: var(--el-text-color-secondary); }

.qr-block { margin-bottom: 14px; &:last-of-type { margin-bottom: 4px; } }
.qr-block__header { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 2px; }
.qr-block__text { font-size: 14px; color: var(--el-text-color-primary); margin-bottom: 8px; }

.qr-options { display: flex; flex-direction: column; gap: 6px; }
.qr-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  text-align: left;
  color: inherit;
}
.qr-option--checked {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.qr-option__mark {
  flex-shrink: 0;
  width: 16px; height: 16px;
  margin-top: 1px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-darker);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px;
  color: var(--el-color-primary);
  .qr-option--checked & { border-color: var(--el-color-primary); }
}
.qr-option__body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.qr-option__label { font-size: 13px; font-weight: 500; color: var(--el-text-color-primary); }
.qr-option__desc { font-size: 12px; color: var(--el-text-color-secondary); }
.qr-option__unanswered { font-size: 12px; color: var(--el-text-color-secondary); padding: 2px 0; }
</style>
