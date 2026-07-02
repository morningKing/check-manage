<!-- src/components/kefu/KefuInstanceDialog.vue -->
<template>
  <el-dialog :model-value="modelValue" :title="instance ? '编辑客服' : '新建客服'" width="640px"
             @update:model-value="$emit('update:modelValue', $event)">
    <el-form label-width="96px">
      <el-form-item label="标识 slug">
        <el-input v-model="slug" data-test="slug" :disabled="!!instance" placeholder="小写字母/数字/连字符，如 presale" />
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="name" placeholder="客服名称" />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="enabled" />
      </el-form-item>
      <el-form-item label="欢迎语">
        <el-input v-model="welcome" type="textarea" :rows="2" placeholder="访客打开时的欢迎语" />
      </el-form-item>
      <el-form-item label="Agent">
        <el-select v-model="agent" clearable placeholder="使用 OpenCode 默认 Agent" style="width:100%">
          <el-option v-for="a in agents" :key="a.name" :label="a.name" :value="a.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="模型">
        <el-select v-model="model" clearable filterable placeholder="使用默认模型" style="width:100%">
          <el-option v-for="m in models" :key="m.id" :label="m.label" :value="m.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="系统提示词">
        <el-input v-model="systemPrompt" type="textarea" :rows="4" placeholder="客服人设，会与固定护栏拼接" />
      </el-form-item>
      <el-form-item label="品牌 logo">
        <el-input v-model="logoUrl" placeholder="logo 图片 URL（可空）" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!canSave" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/kefu'
import type { KefuInstanceFull, KefuInstance } from '@/api/kefu'
import { listAgents, listModels } from '@/api/aiChat'
import type { AgentInfo, ModelInfo } from '@/api/aiChat'

const props = defineProps<{ modelValue: boolean; instance: KefuInstanceFull | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved', inst: KefuInstance): void }>()

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/
const slug = ref(''); const name = ref(''); const enabled = ref(true)
const welcome = ref(''); const agent = ref(''); const model = ref('')
const systemPrompt = ref(''); const logoUrl = ref(''); const saving = ref(false)
const agents = ref<AgentInfo[]>([]); const models = ref<ModelInfo[]>([])

const canSave = computed(() =>
  name.value.trim().length > 0 && (props.instance !== null || SLUG_RE.test(slug.value.trim())))

function reset() {
  const i = props.instance
  slug.value = i?.slug || ''
  name.value = i?.name || ''
  enabled.value = i ? i.enabled : true
  welcome.value = i?.welcome_message || ''
  agent.value = i?.agent || ''
  model.value = i?.model || ''
  systemPrompt.value = i?.system_prompt || ''
  logoUrl.value = i?.branding?.logo || ''
}
watch(() => props.modelValue, v => { if (v) reset() }, { immediate: true })

async function save() {
  if (!canSave.value) return
  saving.value = true
  const branding = { ...(props.instance?.branding || {}), logo: logoUrl.value.trim() }
  const base = {
    name: name.value.trim(), enabled: enabled.value, welcome_message: welcome.value,
    agent: agent.value || null, model: model.value || null,
    system_prompt: systemPrompt.value || null, branding,
  }
  try {
    const result = props.instance
      ? await api.updateInstance(props.instance.id, base)
      : await api.createInstance({ slug: slug.value.trim(), ...base })
    ElMessage.success(props.instance ? '已保存' : '客服已创建')
    emit('saved', result as KefuInstance)
    emit('update:modelValue', false)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '操作失败')
  } finally {
    saving.value = false
  }
}

async function loadOptions() {
  try { agents.value = (await listAgents()).agents } catch { /* non-fatal */ }
  try { models.value = (await listModels()).models } catch { /* non-fatal */ }
}
loadOptions()
</script>
