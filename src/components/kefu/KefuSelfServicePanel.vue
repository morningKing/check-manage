<!-- src/components/kefu/KefuSelfServicePanel.vue -->
<template>
  <div class="kefu-ssp">
    <el-input v-model="filter" placeholder="搜索常见问题" clearable />
    <div class="tags">
      <el-tag :effect="activeTag===null?'dark':'plain'" @click="activeTag=null">全部</el-tag>
      <el-tag v-for="c in categories" :key="c" :effect="activeTag===c?'dark':'plain'" @click="activeTag=c">{{ c }}</el-tag>
    </div>
    <div v-if="visible.length===0" class="empty">暂无匹配的问题</div>
    <div v-for="it in visible" :key="it.id" class="faq">
      <div class="q" @click="toggle(it.id)">▸ {{ it.question }}</div>
      <div v-if="expandedId===it.id" class="a">
        <MdPreview :modelValue="it.answer" :code-foldable="false" />
        <el-button size="small" @click="askAi(it)">没解决？问 AI</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import type { KefuFaqItem } from '@/api/kefuPublic'

const props = defineProps<{ items: KefuFaqItem[] }>()
const emit = defineEmits<{ (e: 'click', id: string): void; (e: 'escalate', q: string): void }>()

const filter = ref('')
const activeTag = ref<string | null>(null)
const expandedId = ref<string | null>(null)

const categories = computed(() => Array.from(new Set(props.items.map(i => i.category).filter((c): c is string => !!c))))
const visible = computed(() => props.items.filter(i =>
  (activeTag.value === null || i.category === activeTag.value) &&
  (filter.value === '' || i.question.includes(filter.value))))

function toggle(id: string) {
  expandedId.value = expandedId.value === id ? null : id
  if (expandedId.value === id) emit('click', id)
}
function askAi(it: KefuFaqItem) { emit('escalate', it.question) }
</script>
