<template>
  <div class="validation-script-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：脚本列表 -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>校验脚本列表</span>
              <div class="header-actions">
                <el-button type="primary" size="small" @click="handleAdd">
                  <el-icon><Plus /></el-icon>
                  新增
                </el-button>
                <el-button type="success" size="small" @click="triggerUpload">
                  <el-icon><Upload /></el-icon>
                  上传脚本
                </el-button>
                <input
                  ref="fileInputRef"
                  type="file"
                  accept=".py"
                  style="display: none"
                  @change="handleFileUpload"
                />
              </div>
            </div>
          </template>

          <div class="script-list">
            <div
              v-for="script in scripts"
              :key="script.id"
              class="script-item"
              :class="{ active: currentScriptId === script.id }"
              @click="handleSelect(script)"
            >
              <div class="script-info">
                <div class="script-name">{{ script.name }}</div>
                <div class="script-meta">{{ script.description || '暂无描述' }}</div>
              </div>
              <div class="script-actions">
                <el-button
                  type="danger"
                  link
                  size="small"
                  @click.stop="handleDeleteConfirm(script)"
                >
                  删除
                </el-button>
              </div>
            </div>

            <el-empty v-if="scripts.length === 0" description="暂无校验脚本" />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：脚本编辑 -->
      <el-col :span="16">
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>{{ currentScriptId ? '编辑脚本' : '脚本详情' }}</span>
            </div>
          </template>

          <div v-if="currentScriptId" class="script-detail">
            <el-form
              ref="formRef"
              :model="formData"
              :rules="formRules"
              label-width="100px"
              class="script-form"
            >
              <el-form-item label="脚本名称" prop="name">
                <el-input
                  v-model="formData.name"
                  placeholder="请输入脚本名称"
                  maxlength="50"
                />
              </el-form-item>

              <el-form-item label="描述" prop="description">
                <el-input
                  v-model="formData.description"
                  type="textarea"
                  placeholder="请输入脚本描述"
                  :rows="2"
                />
              </el-form-item>

              <el-form-item label="脚本代码" class="code-form-item">
                <div class="code-editor-wrapper">
                  <Codemirror
                    v-model="formData.script"
                    :extensions="cmExtensions"
                    :style="{ height: '400px' }"
                    placeholder="请编写 Python 校验脚本..."
                  />
                </div>
              </el-form-item>

              <el-form-item>
                <el-button type="primary" @click="handleSave" :loading="saveLoading">
                  保存
                </el-button>
                <el-button type="success" @click="handleTest" :loading="testLoading">
                  测试
                </el-button>
              </el-form-item>
            </el-form>

            <!-- 测试结果 -->
            <div v-if="testResult" class="test-result">
              <el-divider content-position="left">测试结果</el-divider>
              <el-alert
                v-if="testResult.success"
                type="success"
                title="校验通过，无错误"
                :closable="false"
                show-icon
              />
              <el-alert
                v-else
                type="error"
                :title="`校验失败（${testResult.errors.length} 个错误）`"
                :closable="false"
                show-icon
              />
              <div v-if="testResult.errors.length" class="test-messages">
                <div v-for="(err, i) in testResult.errors" :key="'e'+i" class="test-msg error">
                  <el-icon><CircleCloseFilled /></el-icon> {{ err }}
                </div>
              </div>
              <div v-if="testResult.warnings.length" class="test-messages">
                <div v-for="(w, i) in testResult.warnings" :key="'w'+i" class="test-msg warning">
                  <el-icon><WarningFilled /></el-icon> {{ w }}
                </div>
              </div>
              <div v-if="testResult.pendingRelations.length" class="test-messages">
                <div class="test-msg info">
                  <strong>待建立关联：</strong>{{ testResult.pendingRelations.length }} 组
                </div>
              </div>
            </div>

            <!-- 使用说明 -->
            <el-divider content-position="left">使用说明</el-divider>
            <el-tabs type="border-card" class="help-tabs">
              <el-tab-pane label="快速入门">
                <div class="help-content">
                  <h4>编写流程</h4>
                  <ol>
                    <li>在代码编辑器中编写 Python 校验逻辑</li>
                    <li>通过 <code>add_error('消息')</code> 输出校验错误，<strong>有错误时保存将被阻止</strong></li>
                    <li>通过 <code>add_warning('消息')</code> 输出警告（不阻止保存，仅提示）</li>
                    <li>点击「测试」按钮，用示例数据验证脚本逻辑</li>
                    <li>测试通过后点击「保存」，然后在「页面配置」中将脚本绑定到目标数据页</li>
                  </ol>
                  <h4>最小示例</h4>
<pre class="help-code"># 校验：名称不能为空
if not record.get('caseName'):
    add_error('用例名称不能为空')</pre>
                  <p>用户提交数据时，如果 <code>caseName</code> 为空，前端会弹出错误提示，数据不会被保存。</p>
                  <h4>注意事项</h4>
                  <ul>
                    <li>不允许使用 <code>import</code> 语句，所有可用模块已预先注入（json, re, math, collections, datetime, timedelta, <strong>pd</strong>, <strong>np</strong>）</li>
                    <li>脚本执行超时时间为 <strong>60 秒</strong></li>
                    <li>禁止使用 <code>open()</code>、<code>exec()</code>、<code>eval()</code> 等危险函数</li>
                    <li>脚本对数据库的访问为<strong>只读</strong>（通过 query 类函数），写入仅限 <code>set_relations()</code></li>
                  </ul>
                </div>
              </el-tab-pane>

              <el-tab-pane label="变量参考">
                <div class="help-content">
                  <h4>入参变量（系统自动注入）</h4>
                  <table class="help-table">
                    <thead>
                      <tr><th>变量名</th><th>类型</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>record</code></td>
                        <td><code>dict</code></td>
                        <td>当前提交的数据。键为字段名，值为用户填写的内容。不含 <code>id</code> 和 <code>createdAt</code></td>
                      </tr>
                      <tr>
                        <td><code>action</code></td>
                        <td><code>str</code></td>
                        <td>操作类型：<code>'create'</code>（新增）或 <code>'update'</code>（修改）</td>
                      </tr>
                      <tr>
                        <td><code>old_data</code></td>
                        <td><code>dict | None</code></td>
                        <td>修改前的旧数据。新增时为 <code>None</code>，修改时为修改前的完整数据字典</td>
                      </tr>
                      <tr>
                        <td><code>fields</code></td>
                        <td><code>list[dict]</code></td>
                        <td>当前页面的字段配置列表。每个字段含 <code>fieldName</code>、<code>label</code>、<code>controlType</code>、<code>options</code> 等</td>
                      </tr>
                      <tr>
                        <td><code>collection</code></td>
                        <td><code>str</code></td>
                        <td>当前数据页的集合名称（如 <code>inspection-case</code>）</td>
                      </tr>
                    </tbody>
                  </table>

                  <h4>record 示例</h4>
<pre class="help-code">{
    "caseName": "服务器巡检",
    "caseType": "hardware",
    "priority": "high",
    "description": "检查服务器硬件状态"
}</pre>

                  <h4>可用内置模块</h4>
                  <p>
                    <code>json</code>、<code>re</code>、<code>math</code>、<code>collections</code>、<code>datetime</code>、<code>timedelta</code>
                  </p>
                  <h4>可用内置函数</h4>
                  <p>
                    <code>len</code>、<code>str</code>、<code>int</code>、<code>float</code>、<code>bool</code>、<code>list</code>、<code>dict</code>、<code>tuple</code>、<code>set</code>、<code>sorted</code>、<code>reversed</code>、<code>enumerate</code>、<code>zip</code>、<code>map</code>、<code>filter</code>、<code>range</code>、<code>min</code>、<code>max</code>、<code>sum</code>、<code>abs</code>、<code>round</code>、<code>isinstance</code>、<code>hasattr</code>
                  </p>
                </div>
              </el-tab-pane>

              <el-tab-pane label="函数接口">
                <div class="help-content">
                  <h4>校验输出函数</h4>
                  <table class="help-table">
                    <thead>
                      <tr><th>函数</th><th>参数</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>add_error(msg)</code></td>
                        <td><code>msg: str</code></td>
                        <td><strong>添加校验错误。只要有一个 error，数据就不会被保存</strong></td>
                      </tr>
                      <tr>
                        <td><code>add_warning(msg)</code></td>
                        <td><code>msg: str</code></td>
                        <td>添加警告信息。不阻止保存，仅提示</td>
                      </tr>
                    </tbody>
                  </table>

                  <h4>数据查询函数</h4>
                  <table class="help-table">
                    <thead>
                      <tr><th>函数</th><th>参数</th><th>返回值</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>query(collection)</code></td>
                        <td><code>collection: str</code></td>
                        <td><code>list[dict]</code></td>
                        <td>查询目标数据页的全部记录</td>
                      </tr>
                      <tr>
                        <td><code>query_one(collection, id)</code></td>
                        <td><code>collection: str</code>, <code>id: str</code></td>
                        <td><code>dict | None</code></td>
                        <td>按 ID 精确查找单条记录</td>
                      </tr>
                      <tr>
                        <td><code>find_by(collection, field, value)</code></td>
                        <td><code>collection: str</code>, <code>field: str</code>, <code>value: any</code></td>
                        <td><code>list[dict]</code></td>
                        <td>按字段值筛选记录</td>
                      </tr>
                      <tr>
                        <td><code>get_relations(collection, id)</code></td>
                        <td><code>collection: str</code>, <code>id: str</code></td>
                        <td><code>dict[str, list[str]]</code></td>
                        <td>查询指定记录的现有关联关系</td>
                      </tr>
                    </tbody>
                  </table>

                  <h4>关联操作函数</h4>
                  <table class="help-table">
                    <thead>
                      <tr><th>函数</th><th>参数</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>set_relations(field, target_col, target_field, ids)</code></td>
                        <td>
                          <code>field: str</code> — 当前记录的关联字段名<br/>
                          <code>target_col: str</code> — 目标数据页集合名<br/>
                          <code>target_field: str</code> — 目标的反向关联字段名<br/>
                          <code>ids: list[str]</code> — 目标记录 ID 列表
                        </td>
                        <td>数据保存成功后自动建立<strong>双向关联</strong>。替换该字段的全部旧关联</td>
                      </tr>
                    </tbody>
                  </table>
                  <p><strong>注意</strong>：<code>set_relations</code> 仅在校验通过、数据保存成功后才会执行。如果 <code>add_error</code> 被调用，关联操作也会被取消。</p>
                </div>
              </el-tab-pane>

              <el-tab-pane label="完整示例">
                <div class="help-content">
                  <h4>基础校验 — 必填和格式</h4>
<pre class="help-code"># 必填校验
if not record.get('caseName'):
    add_error('用例名称不能为空')

# 格式校验（正则）
phone = record.get('contactPhone', '')
if phone and not re.match(r'^1[3-9]\d{9}$', phone):
    add_error('联系电话格式不正确')

# 数值范围校验
score = record.get('score')
if score is not None:
    try:
        score = float(score)
        if score &lt; 0 or score &gt; 100:
            add_error('分数必须在 0~100 之间')
    except (ValueError, TypeError):
        add_error('分数必须是数字')</pre>

                  <h4>区分新增和修改</h4>
<pre class="help-code"># 新增时：检查名称是否重复
if action == 'create':
    existing = find_by(collection, 'caseName', record.get('caseName'))
    if existing:
        add_error('用例名称已存在')

# 修改时：不允许降低优先级
if action == 'update' and old_data:
    levels = {'high': 3, 'medium': 2, 'low': 1}
    old_p = levels.get(old_data.get('priority'), 0)
    new_p = levels.get(record.get('priority'), 0)
    if new_p &lt; old_p:
        add_error('不允许降低优先级')</pre>

                  <h4>跨数据页查询</h4>
<pre class="help-code"># 检查引用的项目是否存在
project_id = record.get('projectId')
if project_id:
    project = query_one('projects', project_id)
    if not project:
        add_error('关联的项目不存在')
    elif project.get('status') == 'closed':
        add_warning('该项目已关闭，建议选择活跃项目')</pre>

                  <h4>自动关联推导</h4>
<pre class="help-code"># 根据用例类型自动关联到对应类型的巡检项
case_type = record.get('caseType')
if case_type:
    items = find_by('inspection-items', 'itemType', case_type)
    item_ids = [item['id'] for item in items]
    if item_ids:
        set_relations('relatedItems', 'inspection-items',
                      'relatedCases', item_ids)
    else:
        add_warning('未找到类型为「' + str(case_type) + '」的巡检项')</pre>
                </div>
              </el-tab-pane>

              <el-tab-pane label="上传脚本">
                <div class="help-content">
                  <h4>操作步骤</h4>
                  <ol>
                    <li>点击「上传脚本」按钮，选择本地 .py 文件</li>
                    <li>脚本内容自动填充到编辑器</li>
                    <li>填写脚本名称、描述（文件名会作为默认名称）</li>
                    <li>可继续在线编辑调整代码</li>
                    <li>点击「保存」完成创建</li>
                  </ol>

                  <h4>文件要求</h4>
                  <ul>
                    <li>文件类型：<code>.py</code>（Python 脚本）</li>
                    <li>文件编码：<code>UTF-8</code></li>
                    <li>文件大小：不超过 100KB</li>
                  </ul>

                  <h4>本地开发建议</h4>
                  <p>推荐使用 VSCode 或 PyCharm 编写脚本：</p>
                  <ul>
                    <li>安装 Python 插件获得语法高亮和补全</li>
                    <li>脚本变量参考本页面「变量参考」Tab</li>
                    <li>上传后使用「测试」功能验证脚本正确性</li>
                  </ul>

                  <h4>示例脚本结构</h4>
<pre class="help-code"># ============================================
# 校验脚本
# ============================================
# 入参变量（系统自动注入）：
#   record     : dict         — 当前提交的数据
#   action     : str          — 'create' 或 'update'
#   old_data   : dict | None  — 修改前的旧数据
#   fields     : list[dict]   — 字段配置
#   collection : str          — 当前集合名
#
# 校验输出：
#   add_error(msg)   — 添加错误（阻止保存）
#   add_warning(msg) — 添加警告（不阻止）
# ============================================

# 示例：必填校验
if not record.get('name'):
    add_error('名称不能为空')</pre>
                </div>
              </el-tab-pane>
            </el-tabs>
          </div>

          <el-empty v-else description="请选择或新增校验脚本" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除校验脚本「${scriptToDelete?.name}」吗？已绑定此脚本的页面将自动解除绑定。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Upload, CircleCloseFilled, WarningFilled } from '@element-plus/icons-vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { ConfirmDialog } from '@/components/common'
import {
  getValidationScripts,
  createValidationScript,
  updateValidationScript,
  deleteValidationScript,
  testValidationScript,
} from '@/api/validationScript'
import type { ValidationScript } from '@/types'

// ==================== CodeMirror 配置 ====================

const cmExtensions = [python(), oneDark]

// ==================== 脚手架模板 ====================

const SCAFFOLD = `# ============================================
# 校验脚本
# ============================================
# 入参变量（系统自动注入）：
#   record     : dict         — 当前提交的数据
#   action     : str          — 'create' 或 'update'
#   old_data   : dict | None  — 修改前的旧数据（仅 update）
#   fields     : list[dict]   — 字段配置
#   collection : str          — 当前集合名
#
# 校验输出：
#   add_error(msg)   — 添加错误（阻止保存）
#   add_warning(msg) — 添加警告（不阻止）
#
# 查询函数：
#   query(collection)               — 查询全部记录
#   query_one(collection, id)       — 按 ID 查询
#   find_by(collection, field, val) — 按字段值查找
#   get_relations(collection, id)   — 查询现有关联
#
# 关联函数：
#   set_relations(field, target_col, target_field, ids)
# ============================================

# 示例：必填校验
# if not record.get('name'):
#     add_error('名称不能为空')
`

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const fileInputRef = ref<HTMLInputElement>()

const scripts = ref<ValidationScript[]>([])
const currentScriptId = ref<string | null>(null)
const formData = ref({
  name: '',
  description: '',
  script: '',
})
const saveLoading = ref(false)
const testLoading = ref(false)
const deleteDialogVisible = ref(false)
const scriptToDelete = ref<ValidationScript | null>(null)
const testResult = ref<{
  success: boolean
  errors: string[]
  warnings: string[]
  pendingRelations: any[]
} | null>(null)

// ==================== 表单校验 ====================

const formRules: FormRules = {
  name: [{ required: true, message: '请输入脚本名称', trigger: 'blur' }],
}

// ==================== 方法 ====================

async function loadScripts() {
  try {
    scripts.value = await getValidationScripts()
  } catch {
    ElMessage.error('加载脚本列表失败')
  }
}

function handleSelect(script: ValidationScript) {
  currentScriptId.value = script.id
  formData.value = {
    name: script.name,
    description: script.description || '',
    script: script.script,
  }
  testResult.value = null
}

function handleAdd() {
  currentScriptId.value = '__new__'
  formData.value = {
    name: '',
    description: '',
    script: SCAFFOLD,
  }
  testResult.value = null
}

function triggerUpload() {
  fileInputRef.value?.click()
}

function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // Validate file type
  if (!file.name.endsWith('.py')) {
    ElMessage.error('仅支持 .py 文件')
    return
  }

  // Validate file size (100KB limit)
  if (file.size > 100 * 1024) {
    ElMessage.error('文件大小不能超过 100KB')
    return
  }

  // Read file content
  const reader = new FileReader()
  reader.onload = (e) => {
    const content = e.target?.result as string
    if (content) {
      // Switch to new mode with content filled
      currentScriptId.value = '__new__'
      formData.value = {
        name: file.name.replace('.py', ''),
        description: '',
        script: content,
      }
      testResult.value = null
      ElMessage.success('脚本已加载，请填写信息后保存')
    }
  }
  reader.onerror = () => {
    ElMessage.error('文件读取失败')
  }
  reader.readAsText(file, 'UTF-8')

  // Clear input to allow re-upload same file
  input.value = ''
}

async function handleSave() {
  const valid = await formRef.value?.validate()
  if (!valid) return

  if (!formData.value.script.trim()) {
    ElMessage.warning('请编写脚本代码')
    return
  }

  saveLoading.value = true
  try {
    if (currentScriptId.value === '__new__') {
      const created = await createValidationScript({
        name: formData.value.name,
        description: formData.value.description,
        script: formData.value.script,
      })
      currentScriptId.value = created.id
      ElMessage.success('创建成功')
    } else {
      await updateValidationScript(currentScriptId.value!, {
        name: formData.value.name,
        description: formData.value.description,
        script: formData.value.script,
      })
      ElMessage.success('保存成功')
    }
    await loadScripts()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

async function handleTest() {
  const valid = await formRef.value?.validate()
  if (!valid) return

  if (!formData.value.script.trim()) {
    ElMessage.warning('请编写脚本代码')
    return
  }

  if (currentScriptId.value === '__new__') {
    ElMessage.warning('请先保存脚本再测试')
    return
  }

  testLoading.value = true
  testResult.value = null
  try {
    const result = await testValidationScript(currentScriptId.value!, {
      record: { name: '示例数据', type: 'test', priority: 'high' },
      action: 'create',
      fields: [
        { fieldName: 'name', label: '名称', controlType: 'text', required: true },
        { fieldName: 'type', label: '类型', controlType: 'select' },
        { fieldName: 'priority', label: '优先级', controlType: 'select' },
      ],
      collection: 'test',
    })
    testResult.value = result
  } catch (e: any) {
    testResult.value = {
      success: false,
      errors: [e.response?.data?.errors?.[0] || '测试执行失败'],
      warnings: [],
      pendingRelations: [],
    }
  } finally {
    testLoading.value = false
  }
}

function handleDeleteConfirm(script: ValidationScript) {
  scriptToDelete.value = script
  deleteDialogVisible.value = true
}

async function handleDelete() {
  if (!scriptToDelete.value) return
  try {
    await deleteValidationScript(scriptToDelete.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    if (currentScriptId.value === scriptToDelete.value.id) {
      currentScriptId.value = null
    }
    await loadScripts()
  } catch {
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  loadScripts()
})
</script>

<style scoped lang="scss">
.validation-script-manager {
  height: 100%;
}

.full-height {
  height: 100%;
}

.list-card,
.detail-card {
  height: 100%;

  :deep(.el-card__body) {
    height: calc(100% - 60px);
    overflow: auto;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .header-actions {
    display: flex;
    gap: 8px;
  }
}

.script-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.script-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: #409eff;
    background-color: #f5f7fa;
  }

  &.active {
    border-color: #409eff;
    background-color: #ecf5ff;
  }

  .script-info {
    .script-name {
      font-weight: 500;
      color: #303133;
    }

    .script-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;
    }
  }

  .script-actions {
    opacity: 0;
    transition: opacity 0.2s;
  }

  &:hover .script-actions {
    opacity: 1;
  }
}

.script-detail {
  .script-form {
    max-width: 900px;
  }
}

.code-form-item {
  :deep(.el-form-item__content) {
    display: block;
  }
}

.code-editor-wrapper {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;

  :deep(.cm-editor) {
    font-size: 13px;

    .cm-scroller {
      font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
    }
  }
}

// ==================== 测试结果 ====================

.test-result {
  margin-top: 16px;
}

.test-messages {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.test-msg {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 13px;

  &.error {
    background: #fef0f0;
    color: #f56c6c;
  }

  &.warning {
    background: #fdf6ec;
    color: #e6a23c;
  }

  &.info {
    background: #f0f9eb;
    color: #67c23a;
  }
}

// ==================== 使用说明 ====================

.help-tabs {
  margin-top: 8px;

  :deep(.el-tabs__content) {
    padding: 16px;
  }
}

.help-content {
  font-size: 13px;
  color: #606266;
  line-height: 1.8;

  h4 {
    margin: 16px 0 8px;
    color: #303133;
    font-size: 14px;

    &:first-child {
      margin-top: 0;
    }
  }

  ol, ul {
    padding-left: 20px;
    margin: 4px 0;
  }

  li {
    margin: 4px 0;
  }

  p {
    margin: 8px 0;
  }

  code {
    background: #f0f2f5;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 12px;
    color: #e6a23c;
  }
}

.help-code {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 4px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  margin: 8px 0;
  white-space: pre;
}

.help-table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;

  th, td {
    border: 1px solid #ebeef5;
    padding: 8px 12px;
    text-align: left;
  }

  th {
    background: #f5f7fa;
    color: #303133;
    font-weight: 500;
  }

  td {
    color: #606266;
  }

  code {
    background: #f0f2f5;
    padding: 1px 4px;
    border-radius: 2px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 12px;
    color: #e6a23c;
  }
}
</style>
