<template>
  <div class="kefu-blocks-editor">
    <div class="toolbar">
      <span>自助区块</span>
      <el-select v-model="newType" placeholder="选择类型" style="width:140px">
        <el-option v-for="t in TYPES" :key="t.value" :value="t.value" :label="t.label" />
      </el-select>
      <el-button @click="addBlock(newType)">新增区块</el-button>
    </div>
    <div v-for="(b, idx) in list" :key="b.id" class="block-row">
      <div class="block-head">
        <el-button link :disabled="idx===0" @click="move(idx,-1)">↑</el-button>
        <el-button link :disabled="idx===list.length-1" @click="move(idx,1)">↓</el-button>
        <span class="btype">{{ labelOf(b.type) }}</span>
        <el-input v-model="b.title" placeholder="区块标题（可空）" style="width:200px" @input="emit" />
        <el-switch v-model="b.enabled" @update:modelValue="emit" />
        <el-button link type="danger" @click="removeBlock(idx)">删除</el-button>
      </div>
      <div class="block-body">
        <!-- links -->
        <template v-if="b.type==='links'">
          <div v-for="(it, j) in (b.config.items || [])" :key="j" class="link-item">
            <el-input v-model="it.icon" placeholder="图标(可空)" style="width:100px" @input="emit" />
            <el-input v-model="it.label" placeholder="名称" style="width:160px" @input="emit" />
            <el-input v-model="it.url" placeholder="https://…" style="width:240px" @input="emit" />
            <el-button link type="danger" @click="delItem(b, j)">×</el-button>
          </div>
          <el-button size="small" @click="addItem(b)">+ 添加入口</el-button>
        </template>
        <!-- faq -->
        <template v-else-if="b.type==='faq'">
          <el-input v-model.number="b.config.limit" type="number" placeholder="展示条数(默认5)" style="width:180px" @input="emit" />
        </template>
        <!-- richtext -->
        <template v-else-if="b.type==='richtext'">
          <MdEditor v-model="b.config.markdown" style="height:200px" @onChange="emit" />
        </template>
        <!-- contact -->
        <template v-else-if="b.type==='contact'">
          <el-input v-model="b.config.phone" placeholder="电话" @input="emit" />
          <el-input v-model="b.config.email" placeholder="邮箱" @input="emit" />
          <el-input v-model="b.config.hours" placeholder="工作时间" @input="emit" />
          <el-input v-model="b.config.wechat" placeholder="微信" @input="emit" />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'

interface Block { id: string; type: string; title: string; enabled: boolean; config: any }
const props = defineProps<{ modelValue: Block[] }>()
const emits = defineEmits<{ (e: 'update:modelValue', v: Block[]): void }>()

const TYPES = [
  { value: 'links', label: '快捷入口' }, { value: 'faq', label: '热点问题' },
  { value: 'richtext', label: '公告/富文本' }, { value: 'contact', label: '联系方式' },
]
const newType = ref('links')
const list = ref<Block[]>(clone(props.modelValue))
watch(() => props.modelValue, v => { list.value = clone(v) })

function clone(v: Block[]) { return JSON.parse(JSON.stringify(v || [])) }
function labelOf(t: string) { return TYPES.find(x => x.value === t)?.label || t }
function emit() { emits('update:modelValue', clone(list.value)) }

function addBlock(type: string) {
  const id = 'blk_' + Math.random().toString(36).slice(2, 8)
  const config = type === 'links' ? { items: [] } : type === 'faq' ? { limit: 5 } : type === 'richtext' ? { markdown: '' } : {}
  list.value.push({ id, type, title: '', enabled: true, config })
  emit()
}
function removeBlock(idx: number) { list.value.splice(idx, 1); emit() }
function move(idx: number, dir: number) {
  const j = idx + dir
  if (j < 0 || j >= list.value.length) return
  const a = list.value
  ;[a[idx], a[j]] = [a[j], a[idx]]; emit()
}
function addItem(b: Block) { (b.config.items ||= []).push({ icon: '', label: '', url: '' }); emit() }
function delItem(b: Block, j: number) { b.config.items?.splice(j, 1); emit() }

defineExpose({ addBlock, removeBlock, move })
</script>
