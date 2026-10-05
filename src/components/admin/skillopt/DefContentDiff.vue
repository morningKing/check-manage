<template>
  <pre class="vcd"><code><span v-for="(ln, i) in lines" :key="i"
    :class="lineClass(ln)">{{ ln || ' ' }}
</span></code></pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ diff: string }>()

const lines = computed(() => props.diff.split('\n'))

/** unified diff 行着色：@@ hunk 头 / --- +++ 文件头 / + 新增 / - 删除 */
function lineClass(ln: string) {
  if (ln.startsWith('@@') || ln.startsWith('--- ') || ln.startsWith('+++ ')) {
    return 'vcd__hunk'
  }
  if (ln.startsWith('+')) return 'vcd__add'
  if (ln.startsWith('-')) return 'vcd__del'
  return ''
}
</script>

<style scoped>
.vcd {
  margin: 0; max-height: 60vh; overflow: auto;
  font-family: monospace; font-size: 12px; line-height: 1.5;
  background: var(--el-fill-color-light); padding: 8px; border-radius: 4px;
}
.vcd__add {
  color: var(--el-color-success);
  background: rgba(103, 194, 58, 0.12);
  display: inline-block; width: 100%;
}
.vcd__del {
  color: var(--el-color-danger);
  background: rgba(245, 108, 108, 0.12);
  display: inline-block; width: 100%;
}
.vcd__hunk { color: var(--el-color-info); font-weight: 600; }
</style>
