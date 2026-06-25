<script setup lang="ts">
import { computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Loading, CircleCheck, Clock, CircleClose } from '@element-plus/icons-vue'
import { todoProgress, type TodoItem } from '@/utils/todos'

const props = defineProps<{ todos: TodoItem[] }>()

const progress = computed(() => todoProgress(props.todos))
const allDone = computed(() => progress.value.total > 0 && progress.value.done === progress.value.total)
</script>

<template>
  <div class="todo-block">
    <div class="todo-block__head">
      <span class="todo-block__title">执行计划</span>
      <span class="todo-block__progress" :class="{ done: allDone }">
        {{ progress.done }}/{{ progress.total }}
      </span>
    </div>
    <ul class="todo-block__list">
      <li
        v-for="(t, i) in todos" :key="i"
        class="todo-item" :class="`todo-item--${t.status}`"
      >
        <ElIcon class="todo-item__icon">
          <CircleCheck v-if="t.status === 'completed'" />
          <Loading v-else-if="t.status === 'in_progress'" class="spin" />
          <CircleClose v-else-if="t.status === 'cancelled'" />
          <Clock v-else />
        </ElIcon>
        <span class="todo-item__text">{{ t.content }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped lang="scss">
.todo-block {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  background: var(--el-fill-color-lighter);
  overflow: hidden;
}
.todo-block__head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.todo-block__title { font-weight: 600; font-size: 13px; color: var(--el-text-color-primary); }
.todo-block__progress {
  margin-left: auto; font-size: 12px; color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
  &.done { color: var(--el-color-success); font-weight: 600; }
}
.todo-block__list { list-style: none; margin: 0; padding: 6px 12px 10px; }
.todo-item {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 3px 0; font-size: 13px; line-height: 1.5;
  color: var(--el-text-color-regular);
}
.todo-item__icon { flex-shrink: 0; margin-top: 2px; }
.todo-item__text { word-break: break-word; }

.todo-item--completed { color: var(--el-text-color-secondary); }
.todo-item--completed .todo-item__icon { color: var(--el-color-success); }

/* in-progress is the one that matters most: highlight it so the user sees the
   current step at a glance. */
.todo-item--in_progress { color: var(--el-color-primary); font-weight: 600; }
.todo-item--in_progress .todo-item__icon { color: var(--el-color-primary); }

.todo-item--pending .todo-item__icon { color: var(--el-text-color-placeholder); }

.todo-item--cancelled { color: var(--el-text-color-placeholder); text-decoration: line-through; }
.todo-item--cancelled .todo-item__icon { color: var(--el-text-color-placeholder); }

.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
