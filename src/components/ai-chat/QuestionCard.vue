<script setup lang="ts">
import { reactive, computed } from 'vue'
import { ElIcon, ElInput, ElButton, ElMessage } from 'element-plus'
import { ChatDotRound, Check } from '@element-plus/icons-vue'
import type { QuestionRequest } from '@/api/aiChat'

const props = defineProps<{ request: QuestionRequest }>()
const emit = defineEmits<{ reply: [answers: string[][]]; reject: [] }>()

// Sentinel for the synthetic "其他" option — swapped for the typed text at
// submit time. Kept out of the real option label space (labels are 1-5 word
// model-generated strings per QuestionInfo.options, this can't collide).
const CUSTOM = '__custom__'

// One entry per question: selected[i] holds chosen option labels (radio
// questions cap this at length 1); customText[i] holds the free-typed answer
// when CUSTOM is among the selections.
const selected = reactive<string[][]>(props.request.questions.map(() => []))
const customText = reactive<string[]>(props.request.questions.map(() => ''))

function isChecked(qi: number, value: string): boolean {
  return selected[qi].includes(value)
}

function pick(qi: number, value: string) {
  const q = props.request.questions[qi]
  if (q.multiple) {
    selected[qi] = isChecked(qi, value)
      ? selected[qi].filter(v => v !== value)
      : [...selected[qi], value]
  } else {
    selected[qi] = isChecked(qi, value) ? [] : [value]
  }
}

// null = not yet answerable (something required is still missing)
const answers = computed<string[][] | null>(() => {
  const out: string[][] = []
  for (let i = 0; i < props.request.questions.length; i++) {
    const picks = selected[i]
    if (picks.length === 0) return null
    const resolved = picks.map(v => (v === CUSTOM ? customText[i].trim() : v))
    if (resolved.some(v => !v)) return null // CUSTOM picked but text is empty
    out.push(resolved)
  }
  return out
})

const canSubmit = computed(() => answers.value !== null)

function submit() {
  if (!answers.value) {
    ElMessage.warning('请先回答完所有问题')
    return
  }
  emit('reply', answers.value)
}
</script>

<template>
  <div class="question-card">
    <div class="question-card__head">
      <ElIcon class="question-card__icon"><ChatDotRound /></ElIcon>
      <span>AI 想请你选择</span>
    </div>

    <div v-for="(q, qi) in request.questions" :key="qi" class="question-block">
      <div class="question-block__header">{{ q.header }}</div>
      <div class="question-block__text">{{ q.question }}</div>

      <div class="question-options">
        <button
          v-for="opt in q.options" :key="opt.label" type="button"
          class="question-option" :class="{ 'question-option--checked': isChecked(qi, opt.label) }"
          @click="pick(qi, opt.label)"
        >
          <span class="question-option__mark">
            <ElIcon v-if="isChecked(qi, opt.label)"><Check /></ElIcon>
          </span>
          <span class="question-option__body">
            <span class="question-option__label">{{ opt.label }}</span>
            <span v-if="opt.description" class="question-option__desc">{{ opt.description }}</span>
          </span>
        </button>

        <button
          v-if="q.custom !== false" type="button"
          class="question-option" :class="{ 'question-option--checked': isChecked(qi, CUSTOM) }"
          @click="pick(qi, CUSTOM)"
        >
          <span class="question-option__mark">
            <ElIcon v-if="isChecked(qi, CUSTOM)"><Check /></ElIcon>
          </span>
          <span class="question-option__body">
            <span class="question-option__label">其他</span>
            <ElInput
              v-if="isChecked(qi, CUSTOM)"
              v-model="customText[qi]" size="small" placeholder="输入你的答案"
              class="question-option__input" @click.stop
            />
          </span>
        </button>
      </div>
    </div>

    <div class="question-card__actions">
      <ElButton size="small" @click="emit('reject')">跳过</ElButton>
      <ElButton size="small" type="primary" :disabled="!canSubmit" @click="submit">提交</ElButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
.question-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0 24px;
  padding: 12px 14px;
  background: var(--el-fill-color-lighter);
  max-width: 780px;
}
.question-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 10px;
}
.question-card__icon { color: var(--el-color-primary); }

.question-block { margin-bottom: 14px; &:last-of-type { margin-bottom: 8px; } }
.question-block__header { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 2px; }
.question-block__text { font-size: 14px; color: var(--el-text-color-primary); margin-bottom: 8px; }

.question-options { display: flex; flex-direction: column; gap: 6px; }
.question-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  cursor: pointer;
  text-align: left;
  width: 100%;
  font: inherit;
  color: inherit;
  transition: border-color 0.15s, background 0.15s;
  &:hover { border-color: var(--el-color-primary-light-5); }
}
.question-option--checked {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.question-option__mark {
  flex-shrink: 0;
  width: 16px; height: 16px;
  margin-top: 1px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-darker);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px;
  color: var(--el-color-primary);
  .question-option--checked & { border-color: var(--el-color-primary); }
}
.question-option__body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.question-option__label { font-size: 13px; font-weight: 500; color: var(--el-text-color-primary); }
.question-option__desc { font-size: 12px; color: var(--el-text-color-secondary); }
.question-option__input { margin-top: 4px; }

.question-card__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
