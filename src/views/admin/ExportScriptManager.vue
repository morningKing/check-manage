<template>
  <div class="export-script-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：脚本列表 -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>导出脚本列表</span>
              <div>
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
                <div class="script-meta">
                  <el-tag size="small" type="info">{{ script.outputFormat }}</el-tag>
                  <el-tag size="small" :type="scopeTagType(script.scope)">
                    {{ scopeLabel(script.scope) }}
                  </el-tag>
                </div>
                <div class="script-id" @click.stop="copyId(script.id)" title="点击复制 ID">
                  {{ script.id }}
                </div>
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

            <el-empty v-if="scripts.length === 0" description="暂无导出脚本" />
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

              <el-form-item label="输出格式" prop="outputFormat">
                <el-select v-model="formData.outputFormat" placeholder="选择输出格式">
                  <el-option label="JSON" value="json" />
                  <el-option label="XML" value="xml" />
                  <el-option label="CSV" value="csv" />
                  <el-option label="TXT" value="txt" />
                  <el-option label="HTML" value="html" />
                </el-select>
              </el-form-item>

              <el-form-item label="导出维度" prop="scope">
                <el-select v-model="formData.scope" placeholder="选择导出维度">
                  <el-option label="整页数据" value="page" />
                  <el-option label="单行数据" value="row" />
                  <el-option label="菜单级（多表聚合）" value="menu" />
                </el-select>
                <div class="form-tip">
                  <template v-if="formData.scope === 'page'">
                    逐表导出，每个数据表生成一个文件
                  </template>
                  <template v-else-if="formData.scope === 'row'">
                    单行数据导出，在数据表格中点击行导出按钮使用
                  </template>
                  <template v-else-if="formData.scope === 'menu'">
                    菜单级导出，接收菜单下所有数据表的数据，可统一处理
                  </template>
                </div>
              </el-form-item>

              <el-form-item label="脚本代码" class="code-form-item">
                <div class="code-editor-wrapper">
                  <Codemirror
                    v-model="formData.script"
                    :extensions="cmExtensions"
                    :style="{ height: '400px' }"
                    placeholder="请编写 Python 导出脚本..."
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
                :title="`文件名: ${testResult.filename} | 大小: ${testResult.size} 字节`"
                :closable="false"
                show-icon
              />
              <el-alert
                v-else
                type="error"
                :title="testResult.error"
                :closable="false"
                show-icon
              />
              <pre v-if="testResult.success && testResult.preview" class="test-preview">{{ testResult.preview }}</pre>
            </div>

            <!-- 使用说明 -->
            <el-divider content-position="left">使用说明</el-divider>
            <el-tabs type="border-card" class="help-tabs">
              <el-tab-pane label="快速入门">
                <div class="help-content">
                  <h4>编写流程</h4>
                  <ol>
                    <li>选择输出格式（JSON / CSV / XML 等），系统会自动生成对应的脚手架代码</li>
                    <li>在代码编辑器中编写导出逻辑，处理 <code>data</code> 数据并赋值给 <code>result</code></li>
                    <li>点击「测试」按钮，系统将使用示例数据运行脚本并预览结果</li>
                    <li>测试通过后点击「保存」，然后在「页面配置」中将脚本绑定到目标数据页</li>
                  </ol>
                  <h4>最小示例</h4>
                  <pre class="help-code">result = json.dumps(data, ensure_ascii=False, indent=2)</pre>
                  <p>只需一行代码即可完成 JSON 导出。<code>data</code> 变量由系统自动注入，包含当前数据页的全部记录。</p>
                  <h4>注意事项</h4>
                  <ul>
                    <li>脚本<strong>必须</strong>设置 <code>result</code> 变量，类型为 <code>str</code> 或 <code>bytes</code></li>
                    <li>不允许使用 <code>import</code> 语句，所有可用模块已预先注入（json, csv, io, re, math, collections, ET, minidom, datetime, timedelta, <strong>pd</strong>, <strong>np</strong>）</li>
                    <li>脚本执行超时时间为 <strong>60 秒</strong>，菜单级导出脚本为 <strong>300 秒</strong></li>
                    <li>禁止使用 <code>open()</code>、<code>exec()</code>、<code>eval()</code> 等危险函数</li>
                  </ul>
                </div>
              </el-tab-pane>

              <el-tab-pane label="变量参考">
                <div class="help-content">
                  <h4>入参变量（系统自动注入）</h4>

                  <el-alert type="info" :closable="false" style="margin-bottom: 16px;">
                    <template #title>
                      <strong>整页/单行模式</strong>：使用 <code>data</code>、<code>fields</code>、<code>page_name</code>
                    </template>
                  </el-alert>

                  <table class="help-table">
                    <thead>
                      <tr><th>变量名</th><th>类型</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>data</code></td>
                        <td><code>list[dict]</code></td>
                        <td>当前数据页的全部记录。每条记录是一个字典，包含 <code>id</code>、各字段值、<code>createdAt</code></td>
                      </tr>
                      <tr>
                        <td><code>fields</code></td>
                        <td><code>list[dict]</code></td>
                        <td>字段配置列表。每个字段含 <code>fieldName</code>（字段名）、<code>label</code>（显示标签）、<code>controlType</code>（控件类型）、<code>options</code>（选项列表）等</td>
                      </tr>
                      <tr>
                        <td><code>page_name</code></td>
                        <td><code>str</code></td>
                        <td>当前数据页名称，可用于生成文件名</td>
                      </tr>
                    </tbody>
                  </table>

                  <el-divider content-position="left">菜单级模式变量</el-divider>

                  <el-alert type="success" :closable="false" style="margin-bottom: 16px;">
                    <template #title>
                      <strong>菜单级模式</strong>：使用 <code>menu_data</code>、<code>menu_name</code>、<code>total_records</code>
                    </template>
                  </el-alert>

                  <table class="help-table">
                    <thead>
                      <tr><th>变量名</th><th>类型</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>menu_data</code></td>
                        <td><code>list[dict]</code></td>
                        <td>菜单下所有数据表的信息列表</td>
                      </tr>
                      <tr>
                        <td><code>menu_name</code></td>
                        <td><code>str</code></td>
                        <td>菜单名称</td>
                      </tr>
                      <tr>
                        <td><code>total_records</code></td>
                        <td><code>int</code></td>
                        <td>总记录数（所有数据表）</td>
                      </tr>
                    </tbody>
                  </table>

                  <h4>menu_data 结构示例</h4>
<pre class="help-code">[
  {
    "collection": "inspection-case",
    "pageName": "巡检用例",
    "records": [...],
    "fields": [...],
    "recordCount": 26997
  },
  {
    "collection": "inspection-plan",
    "pageName": "巡检计划",
    "records": [...],
    "fields": [...],
    "recordCount": 4
  }
]</pre>

                  <h4>data 示例结构</h4>
<pre class="help-code">[
  {
    "id": "case-1",
    "caseName": "服务器巡检",
    "caseType": "hardware",
    "priority": "high",
    "createdAt": "2024-01-15T08:00:00.000Z"
  },
  ...
]</pre>

                  <h4>fields 示例结构</h4>
<pre class="help-code">[
  {
    "fieldName": "caseName",
    "label": "用例名称",
    "controlType": "text",
    "required": true
  },
  {
    "fieldName": "caseType",
    "label": "用例类型",
    "controlType": "select",
    "options": [
      {"label": "硬件巡检", "value": "hardware"},
      {"label": "软件巡检", "value": "software"}
    ]
  },
  ...
]</pre>

                  <h4>输出变量</h4>
                  <table class="help-table">
                    <thead>
                      <tr><th>变量名</th><th>类型</th><th>必须</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>result</code></td>
                        <td><code>str | bytes</code></td>
                        <td>是</td>
                        <td>导出文件的内容</td>
                      </tr>
                      <tr>
                        <td><code>filename</code></td>
                        <td><code>str</code></td>
                        <td>否</td>
                        <td>输出文件名。默认为「页面名.格式后缀」（如 <code>巡检用例.json</code>）</td>
                      </tr>
                      <tr>
                        <td><code>content_type</code></td>
                        <td><code>str</code></td>
                        <td>否</td>
                        <td>MIME 类型。默认根据输出格式自动推断</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </el-tab-pane>

              <el-tab-pane label="模块参考">
                <div class="help-content">
                  <p>以下模块已预先注入，可直接使用（无需 import）：</p>
                  <table class="help-table">
                    <thead>
                      <tr><th>模块名</th><th>说明</th><th>常用 API</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><code>json</code></td>
                        <td>JSON 编解码</td>
                        <td><code>json.dumps()</code>、<code>json.loads()</code></td>
                      </tr>
                      <tr>
                        <td><code>csv</code></td>
                        <td>CSV 读写</td>
                        <td><code>csv.writer()</code>、<code>csv.DictWriter()</code></td>
                      </tr>
                      <tr>
                        <td><code>io</code></td>
                        <td>内存流</td>
                        <td><code>io.StringIO()</code>、<code>io.BytesIO()</code></td>
                      </tr>
                      <tr>
                        <td><code>re</code></td>
                        <td>正则表达式</td>
                        <td><code>re.sub()</code>、<code>re.findall()</code></td>
                      </tr>
                      <tr>
                        <td><code>math</code></td>
                        <td>数学运算</td>
                        <td><code>math.ceil()</code>、<code>math.floor()</code></td>
                      </tr>
                      <tr>
                        <td><code>collections</code></td>
                        <td>集合工具</td>
                        <td><code>collections.Counter()</code>、<code>collections.OrderedDict()</code></td>
                      </tr>
                      <tr>
                        <td><code>ET</code></td>
                        <td>xml.etree.ElementTree</td>
                        <td><code>ET.Element()</code>、<code>ET.SubElement()</code>、<code>ET.tostring()</code></td>
                      </tr>
                      <tr>
                        <td><code>minidom</code></td>
                        <td>xml.dom.minidom</td>
                        <td><code>minidom.parseString().toprettyxml()</code></td>
                      </tr>
                      <tr>
                        <td><code>datetime</code></td>
                        <td>日期时间类</td>
                        <td><code>datetime.now()</code>、<code>datetime.strftime()</code></td>
                      </tr>
                      <tr>
                        <td><code>timedelta</code></td>
                        <td>时间差</td>
                        <td><code>timedelta(days=1)</code></td>
                      </tr>
                    </tbody>
                  </table>

                  <h4>可用内置函数</h4>
                  <p>
                    <code>len</code>、<code>str</code>、<code>int</code>、<code>float</code>、<code>bool</code>、<code>list</code>、<code>dict</code>、<code>tuple</code>、<code>set</code>、<code>sorted</code>、<code>reversed</code>、<code>enumerate</code>、<code>zip</code>、<code>map</code>、<code>filter</code>、<code>range</code>、<code>min</code>、<code>max</code>、<code>sum</code>、<code>abs</code>、<code>round</code>、<code>isinstance</code>、<code>hasattr</code>
                  </p>
                </div>
              </el-tab-pane>

              <el-tab-pane label="完整示例">
                <div class="help-content">
                  <h4>JSON 导出</h4>
<pre class="help-code">result = json.dumps(data, ensure_ascii=False, indent=2)</pre>

                  <h4>CSV 导出（中文表头）</h4>
<pre class="help-code">output = io.StringIO()
writer = csv.writer(output)

# 写入表头（使用字段的中文标签）
headers = [f['label'] for f in fields]
writer.writerow(headers)

# 写入数据行
for row in data:
    writer.writerow([str(row.get(f['fieldName'], '')) for f in fields])

result = output.getvalue()</pre>

                  <h4>XML 导出（格式化）</h4>
<pre class="help-code">root = ET.Element('records')
for record in data:
    item = ET.SubElement(root, 'record')
    for f in fields:
        child = ET.SubElement(item, f['fieldName'])
        val = record.get(f['fieldName'])
        child.text = str(val) if val is not None else ''

raw_xml = ET.tostring(root, encoding='unicode')
result = minidom.parseString(raw_xml).toprettyxml(indent='  ')</pre>

                  <h4>HTML 表格导出</h4>
<pre class="help-code">html = '&lt;!DOCTYPE html&gt;&lt;html&gt;&lt;head&gt;&lt;meta charset="utf-8"&gt;'
html += '&lt;style&gt;table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f5f5f5}&lt;/style&gt;'
html += '&lt;/head&gt;&lt;body&gt;'
html += '&lt;h2&gt;' + page_name + '&lt;/h2&gt;'
html += '&lt;table&gt;&lt;thead&gt;&lt;tr&gt;'
for f in fields:
    html += '&lt;th&gt;' + f['label'] + '&lt;/th&gt;'
html += '&lt;/tr&gt;&lt;/thead&gt;&lt;tbody&gt;'
for row in data:
    html += '&lt;tr&gt;'
    for f in fields:
        html += '&lt;td&gt;' + str(row.get(f['fieldName'], '')) + '&lt;/td&gt;'
    html += '&lt;/tr&gt;'
html += '&lt;/tbody&gt;&lt;/table&gt;&lt;/body&gt;&lt;/html&gt;'
result = html</pre>

                  <h4>select 字段值映射为标签</h4>
<pre class="help-code"># 将 select 字段的 value 映射为中文标签
def get_label(field, value):
    """根据字段配置查找选项标签"""
    if not field.get('options'):
        return str(value) if value is not None else ''
    for opt in field['options']:
        if opt['value'] == value:
            return opt['label']
    return str(value) if value is not None else ''

output = io.StringIO()
writer = csv.writer(output)
headers = [f['label'] for f in fields]
writer.writerow(headers)
for row in data:
    writer.writerow([get_label(f, row.get(f['fieldName'])) for f in fields])
result = output.getvalue()</pre>
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
# 导出脚本 — JSON 格式
# ============================================
# 入参变量（系统自动注入）：
#   data       : list[dict]  — 数据记录
#   fields     : list[dict]  — 字段配置
#   page_name  : str         — 页面名称
#
# 输出变量：
#   result     : str | bytes — 导出内容（必须）
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)</pre>
                </div>
              </el-tab-pane>
            </el-tabs>
          </div>

          <el-empty v-else description="请选择或新增导出脚本" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除导出脚本「${scriptToDelete?.name}」吗？已绑定此脚本的页面将自动解除绑定。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { ConfirmDialog } from '@/components/common'
import {
  getExportScripts,
  createExportScript,
  updateExportScript,
  deleteExportScript,
  testExportScript,
} from '@/api/exportScript'
import type { ExportScript } from '@/types'

// ==================== CodeMirror 配置 ====================

const cmExtensions = [python(), oneDark]

// ==================== 脚手架模板 ====================

const SCAFFOLD_TEMPLATES: Record<string, string> = {
  json: `# ============================================
# 导出脚本 — JSON 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   data       : list[dict]  — 当前数据页的全部记录
#   fields     : list[dict]  — 字段配置（含 fieldName, label, controlType, options 等）
#   page_name  : str         — 页面名称
#
# 必须设置的输出变量：
#   result     : str | bytes — 导出文件内容
# 可选输出变量：
#   filename     : str — 文件名（默认：页面名.格式后缀）
#   content_type : str — MIME 类型（默认根据格式推断）
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)
`,
  csv: `# ============================================
# 导出脚本 — CSV 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   data       : list[dict]  — 当前数据页的全部记录
#   fields     : list[dict]  — 字段配置（含 fieldName, label, controlType, options 等）
#   page_name  : str         — 页面名称
#
# 必须设置的输出变量：
#   result     : str | bytes — 导出文件内容
# ============================================

output = io.StringIO()
writer = csv.writer(output)

# 写入表头（使用字段的中文标签）
headers = [f['label'] for f in fields]
writer.writerow(headers)

# 写入数据行
for row in data:
    writer.writerow([str(row.get(f['fieldName'], '')) for f in fields])

result = output.getvalue()
`,
  xml: `# ============================================
# 导出脚本 — XML 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   data       : list[dict]  — 当前数据页的全部记录
#   fields     : list[dict]  — 字段配置（含 fieldName, label, controlType, options 等）
#   page_name  : str         — 页面名称
#
# 必须设置的输出变量：
#   result     : str | bytes — 导出文件内容
# ============================================

root = ET.Element('records')
for record in data:
    item = ET.SubElement(root, 'record')
    for f in fields:
        child = ET.SubElement(item, f['fieldName'])
        val = record.get(f['fieldName'])
        child.text = str(val) if val is not None else ''

raw_xml = ET.tostring(root, encoding='unicode')
result = minidom.parseString(raw_xml).toprettyxml(indent='  ')
`,
  txt: `# ============================================
# 导出脚本 — TXT 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   data       : list[dict]  — 当前数据页的全部记录
#   fields     : list[dict]  — 字段配置（含 fieldName, label, controlType, options 等）
#   page_name  : str         — 页面名称
#
# 必须设置的输出变量：
#   result     : str | bytes — 导出文件内容
# ============================================

lines = []
for record in data:
    parts = [f['label'] + ': ' + str(record.get(f['fieldName'], '')) for f in fields]
    lines.append(' | '.join(parts))

result = '\\n'.join(lines)
`,
  html: `# ============================================
# 导出脚本 — HTML 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   data       : list[dict]  — 当前数据页的全部记录
#   fields     : list[dict]  — 字段配置（含 fieldName, label, controlType, options 等）
#   page_name  : str         — 页面名称
#
# 必须设置的输出变量：
#   result     : str | bytes — 导出文件内容
# ============================================

html = '<!DOCTYPE html><html><head><meta charset="utf-8">'
html += '<style>table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f5f5f5}</style>'
html += '</head><body>'
html += '<h2>' + page_name + '</h2>'
html += '<table><thead><tr>'
for f in fields:
    html += '<th>' + f['label'] + '</th>'
html += '</tr></thead><tbody>'
for row in data:
    html += '<tr>'
    for f in fields:
        html += '<td>' + str(row.get(f['fieldName'], '')) + '</td>'
    html += '</tr>'
html += '</tbody></table></body></html>'
result = html
`,
  // 菜单级脚本模板
  menu_json: `# ============================================
# 菜单级导出脚本 — JSON 格式
# ============================================
# 入参变量（系统自动注入，无需定义）：
#   menu_data    : list[dict] — 菜单下所有数据表的信息
#     每个元素包含：
#       - collection   : str        — 数据集合名
#       - pageName     : str        — 页面名称
#       - records      : list[dict] — 该表的所有记录
#       - fields       : list[dict] — 字段配置
#       - recordCount  : int        — 记录数
#   menu_name    : str         — 菜单名称
#   total_records: int         — 总记录数
#
# 输出方式1 - 单文件：
#   result   : str | bytes — 导出文件内容
#   filename : str         — 文件名（可选）
#
# 输出方式2 - 多文件（返回列表）：
#   result = [
#     {'filename': '巡检用例.json', 'content': '...'},
#     {'filename': '巡检计划.csv', 'content': '...'},
#   ]
# ============================================

# 示例：将所有数据表合并为一个 JSON 文件
output = {
    'menuName': menu_name,
    'exportTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'totalRecords': total_records,
    'tables': []
}

for table in menu_data:
    output['tables'].append({
        'pageName': table['pageName'],
        'collection': table['collection'],
        'recordCount': table['recordCount'],
        'records': table['records']
    })

result = json.dumps(output, ensure_ascii=False, indent=2)
filename = menu_name + '_export.json'
`,
  menu_csv: `# ============================================
# 菜单级导出脚本 — 多文件 CSV 格式
# ============================================
# 菜单级脚本可以输出多个文件
# result 为列表时，每个元素包含 filename 和 content
# ============================================

result = []

for table in menu_data:
    output = io.StringIO()
    writer = csv.writer(output)

    # 写入表头
    if table['fields']:
        headers = [f['label'] for f in table['fields']]
        writer.writerow(headers)

    # 写入数据行
    for row in table['records']:
        if table['fields']:
            writer.writerow([str(row.get(f['fieldName'], '')) for f in table['fields']])

    result.append({
        'filename': table['pageName'] + '.csv',
        'content': output.getvalue()
    })
`,
}

/**
 * 获取所有脚手架值的集合（用于判断当前内容是否为未修改的脚手架）
 */
const scaffoldValues = new Set(Object.values(SCAFFOLD_TEMPLATES))

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const fileInputRef = ref<HTMLInputElement>()

const scripts = ref<ExportScript[]>([])
const currentScriptId = ref<string | null>(null)
const formData = ref({
  name: '',
  description: '',
  outputFormat: 'json',
  scope: 'page' as 'page' | 'row' | 'menu',
  script: '',
})
const saveLoading = ref(false)
const testLoading = ref(false)
const deleteDialogVisible = ref(false)
const scriptToDelete = ref<ExportScript | null>(null)
const testResult = ref<{
  success: boolean
  preview?: string
  filename?: string
  contentType?: string
  size?: number
  error?: string
} | null>(null)

// ==================== 表单校验 ====================

const formRules: FormRules = {
  name: [{ required: true, message: '请输入脚本名称', trigger: 'blur' }],
  outputFormat: [{ required: true, message: '请选择输出格式', trigger: 'change' }],
}

// ==================== Scope 辅助函数 ====================

function scopeLabel(scope: string): string {
  const labels: Record<string, string> = {
    'page': '整页',
    'row': '单行',
    'menu': '菜单级'
  }
  return labels[scope] || scope
}

function scopeTagType(scope: string): '' | 'success' | 'warning' | 'info' | 'danger' {
  const types: Record<string, '' | 'success' | 'warning' | 'info' | 'danger'> = {
    'page': '',
    'row': 'warning',
    'menu': 'success'
  }
  return types[scope] || ''
}

// ==================== 格式切换时自动更新脚手架 ====================

function getScaffoldKey(format: string, scope: string): string {
  if (scope === 'menu') {
    return `menu_${format}`
  }
  return format
}

watch(
  () => formData.value.outputFormat,
  (newFormat, oldFormat) => {
    if (!newFormat || !oldFormat) return
    const oldKey = getScaffoldKey(oldFormat, formData.value.scope)
    const newKey = getScaffoldKey(newFormat, formData.value.scope)
    // 仅当当前内容是旧格式的脚手架（未被用户修改）时才自动切换
    const oldScaffold = SCAFFOLD_TEMPLATES[oldKey]
    if (formData.value.script === oldScaffold || formData.value.script === '') {
      formData.value.script = SCAFFOLD_TEMPLATES[newKey] || SCAFFOLD_TEMPLATES['json']
    }
  }
)

// scope 变化时也切换脚手架
watch(
  () => formData.value.scope,
  (newScope, oldScope) => {
    if (!newScope || !oldScope) return
    const oldKey = getScaffoldKey(formData.value.outputFormat, oldScope)
    const newKey = getScaffoldKey(formData.value.outputFormat, newScope)
    const oldScaffold = SCAFFOLD_TEMPLATES[oldKey]
    if (formData.value.script === oldScaffold || formData.value.script === '') {
      formData.value.script = SCAFFOLD_TEMPLATES[newKey] || SCAFFOLD_TEMPLATES['json']
    }
  }
)

// ==================== 方法 ====================

async function loadScripts() {
  try {
    scripts.value = await getExportScripts()
  } catch {
    ElMessage.error('加载脚本列表失败')
  }
}

async function copyId(id: string) {
  try {
    await navigator.clipboard.writeText(id)
    ElMessage.success('ID 已复制')
  } catch {
    ElMessage.info(id)
  }
}

function handleSelect(script: ExportScript) {
  currentScriptId.value = script.id
  formData.value = {
    name: script.name,
    description: script.description || '',
    outputFormat: script.outputFormat,
    scope: script.scope || 'page',
    script: script.script,
  }
  testResult.value = null
}

function handleAdd() {
  currentScriptId.value = '__new__'
  formData.value = {
    name: '',
    description: '',
    outputFormat: 'json',
    scope: 'page',
    script: SCAFFOLD_TEMPLATES['json'],
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
        outputFormat: 'json',
        scope: 'page',
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

  // 手动校验脚本非空（CodeMirror 不走 el-form 校验）
  if (!formData.value.script.trim()) {
    ElMessage.warning('请编写脚本代码')
    return
  }

  // 如果内容仍是脚手架原文，提醒用户
  if (scaffoldValues.has(formData.value.script)) {
    ElMessage.warning('请修改脚手架代码后再保存')
    return
  }

  saveLoading.value = true
  try {
    if (currentScriptId.value === '__new__') {
      const created = await createExportScript({
        name: formData.value.name,
        description: formData.value.description,
        outputFormat: formData.value.outputFormat,
        scope: formData.value.scope,
        script: formData.value.script,
      })
      currentScriptId.value = created.id
      ElMessage.success('创建成功')
    } else {
      await updateExportScript(currentScriptId.value!, {
        name: formData.value.name,
        description: formData.value.description,
        outputFormat: formData.value.outputFormat,
        scope: formData.value.scope,
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
    const result = await testExportScript(currentScriptId.value!, {
      data: [
        { id: 'test-1', name: '示例数据1', value: '100' },
        { id: 'test-2', name: '示例数据2', value: '200' },
      ],
      fields: [
        { fieldName: 'name', label: '名称', controlType: 'text' },
        { fieldName: 'value', label: '值', controlType: 'text' },
      ],
      pageName: '测试页面',
    })
    testResult.value = result
  } catch (e: any) {
    testResult.value = {
      success: false,
      error: e.response?.data?.error || '测试执行失败',
    }
  } finally {
    testLoading.value = false
  }
}

function handleDeleteConfirm(script: ExportScript) {
  scriptToDelete.value = script
  deleteDialogVisible.value = true
}

async function handleDelete() {
  if (!scriptToDelete.value) return
  try {
    await deleteExportScript(scriptToDelete.value.id)
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
.export-script-manager {
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
      margin-top: 4px;
      display: flex;
      gap: 4px;
    }

    .script-id {
      margin-top: 3px;
      font-size: 11px;
      color: var(--el-text-color-placeholder);
      font-family: monospace;
      cursor: pointer;
      &:hover { color: var(--el-color-primary); }
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

// ==================== 使用说明样式 ====================

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

// ==================== 测试结果 ====================

.test-result {
  margin-top: 16px;

  .test-preview {
    background: #282c34;
    color: #abb2bf;
    padding: 12px 16px;
    border-radius: 4px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 12px;
    max-height: 300px;
    overflow: auto;
    margin-top: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
}
</style>
