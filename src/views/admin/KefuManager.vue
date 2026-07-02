<template>
  <div class="kefu-manager">
    <el-page-header content="智能客服 · 热门问题" />
    <el-select v-model="activeIid" placeholder="选择客服实例" @change="onInstanceChange" style="width:280px;margin:12px 0">
      <el-option v-for="i in instances" :key="i.id" :label="i.name" :value="i.id" />
    </el-select>
    <el-button type="primary" :disabled="!activeIid" @click="openCreate">新增热问</el-button>
    <el-table :data="faqs" row-key="id" style="margin-top:12px">
      <el-table-column label="排序" width="70">
        <template #default="{ $index }">
          <el-button link :disabled="$index===0" @click="move($index,-1)">↑</el-button>
          <el-button link :disabled="$index===faqs.length-1" @click="move($index,1)">↓</el-button>
        </template>
      </el-table-column>
      <el-table-column prop="question" label="问题" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="120" />
      <el-table-column prop="click_count" label="点击量" width="90" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="(v: boolean) => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 主页配置：提示气泡 + 自助区块 -->
    <template v-if="activeIid">
      <el-divider />
      <div class="home-config-section">
        <div class="section-title">提示气泡（guided_questions）</div>
        <div v-for="(_, idx) in bubbles" :key="idx" class="bubble-row">
          <el-input v-model="bubbles[idx]" placeholder="提示语" style="width:360px" />
          <el-button link :disabled="idx===0" @click="moveBubble(idx,-1)">↑</el-button>
          <el-button link :disabled="idx===bubbles.length-1" @click="moveBubble(idx,1)">↓</el-button>
          <el-button link type="danger" @click="removeBubble(idx)">删除</el-button>
        </div>
        <el-button size="small" style="margin-top:6px" @click="addBubble">+ 添加气泡</el-button>
      </div>

      <div class="home-config-section" style="margin-top:16px">
        <KefuBlocksEditor v-model="blocks" />
      </div>

      <el-button type="primary" style="margin-top:16px" @click="saveHome">保存主页配置</el-button>
    </template>

    <el-dialog v-model="dialog" :title="editing?.id ? '编辑热问' : '新增热问'" width="720px">
      <el-form label-width="72px">
        <el-form-item label="问题"><el-input v-model="form.question" /></el-form-item>
        <el-form-item label="分类"><el-input v-model="form.category" placeholder="可选标签" /></el-form-item>
        <el-form-item label="答案">
          <MdEditor v-model="form.answer" style="height:320px" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog=false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import * as api from '@/api/kefu'
import type { KefuFaq, KefuInstance } from '@/api/kefu'
import KefuBlocksEditor from '@/components/kefu/KefuBlocksEditor.vue'

const instances = ref<KefuInstance[]>([])
const activeIid = ref('')
const faqs = ref<KefuFaq[]>([])
const dialog = ref(false)
const editing = ref<KefuFaq | null>(null)
const form = reactive({ question: '', answer: '', category: '', enabled: true })

const bubbles = ref<string[]>([])
const blocks = ref<any[]>([])

async function loadInstances() { instances.value = (await api.listInstances()).instances }
async function loadFaq() { if (activeIid.value) faqs.value = (await api.listFaq(activeIid.value)).items }
async function loadHome() {
  if (!activeIid.value) return
  const inst = await api.getInstance(activeIid.value)
  bubbles.value = inst.guided_questions || []
  blocks.value = inst.panel_blocks || []
}

async function onInstanceChange() {
  await loadFaq()
  await loadHome()
}

function addBubble() { bubbles.value.push('') }
function removeBubble(idx: number) { bubbles.value.splice(idx, 1) }
function moveBubble(idx: number, dir: number) {
  const j = idx + dir
  if (j < 0 || j >= bubbles.value.length) return
  const arr = bubbles.value
  ;[arr[idx], arr[j]] = [arr[j], arr[idx]]
}

async function saveHome() {
  await api.updateInstance(activeIid.value, { guided_questions: bubbles.value, panel_blocks: blocks.value })
  ElMessage.success('主页配置已保存')
}

function openCreate() { editing.value = null; Object.assign(form, { question: '', answer: '', category: '', enabled: true }); dialog.value = true }
function openEdit(row: KefuFaq) { editing.value = row; Object.assign(form, { question: row.question, answer: row.answer, category: row.category || '', enabled: row.enabled }); dialog.value = true }

async function save() {
  if (!form.question.trim() || !form.answer.trim()) { ElMessage.warning('问题与答案必填'); return }
  const payload = { ...form, category: form.category || null }
  if (editing.value) await api.updateFaq(activeIid.value, editing.value.id, payload)
  else await api.createFaq(activeIid.value, payload)
  dialog.value = false; await loadFaq(); ElMessage.success('已保存')
}
async function remove(row: KefuFaq) { await api.deleteFaq(activeIid.value, row.id); await loadFaq() }
async function toggle(row: KefuFaq, v: boolean) { await api.updateFaq(activeIid.value, row.id, { enabled: v }); await loadFaq() }
async function move(idx: number, dir: number) {
  const arr = [...faqs.value]; const j = idx + dir
  ;[arr[idx], arr[j]] = [arr[j], arr[idx]]
  faqs.value = arr
  await api.reorderFaq(activeIid.value, arr.map(f => f.id))
}

onMounted(loadInstances)
</script>
