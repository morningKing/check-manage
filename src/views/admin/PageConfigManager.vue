/**
 * 页面配置管理页面
 *
 * 职责：
 * - 管理页面配置列表
 * - 支持页面配置的增删改
 * - 集成字段配置编辑器
 *
 * 布局：
 * - 左侧：页面配置列表
 * - 右侧：页面详情和字段配置编辑器
 */
<template>
  <div class="page-config-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：页面配置列表（已抽取为独立组件） -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <span>页面配置列表</span>
          </template>
          <PageConfigList
            v-model="currentPageId"
            :configs="pageConfigs"
            @add="handleAdd"
          />
        </el-card>
      </el-col>

      <!-- 右侧：页面详情和字段配置 -->
      <el-col :span="16">
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>{{ detailTitle }}</span>
            </div>
          </template>

          <div v-if="showDetail" class="page-detail">
            <el-tabs v-model="activeTab" type="border-card" class="scrollable-tabs">
              <!-- Tab 1: 基本信息 -->
              <el-tab-pane label="基本信息" name="basic">
                <el-form
                  ref="formRef"
                  :model="formData"
                  :rules="formRules"
                  label-width="100px"
                  class="page-form"
                >
                  <el-form-item label="页面名称" prop="name">
                    <el-input
                      v-model="formData.name"
                      placeholder="请输入页面名称"
                      maxlength="50"
                    />
                  </el-form-item>

                  <el-form-item label="页面描述" prop="description">
                    <el-input
                      v-model="formData.description"
                      type="textarea"
                      placeholder="请输入页面描述"
                      :rows="2"
                    />
                  </el-form-item>

                  <el-form-item label="API端点" prop="apiEndpoint">
                    <el-input
                      v-model="formData.apiEndpoint"
                      placeholder="如：/api/data/inspection-case"
                    />
                  </el-form-item>

                  <el-form-item label="导出脚本">
                    <el-select
                      v-model="formData.exportScripts"
                      multiple
                      clearable
                      placeholder="选择整页导出脚本"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="s in pageExportScripts"
                        :key="s.id"
                        :label="`${s.name} (${s.outputFormat})`"
                        :value="s.id"
                      />
                    </el-select>
                  </el-form-item>

                  <el-form-item label="行级导出">
                    <el-select
                      v-model="formData.rowExportScripts"
                      multiple
                      clearable
                      placeholder="选择单行导出脚本"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="s in rowExportScripts"
                        :key="s.id"
                        :label="`${s.name} (${s.outputFormat})`"
                        :value="s.id"
                      />
                    </el-select>
                  </el-form-item>

                  <el-form-item label="Open API">
                    <el-switch
                      v-model="formData.apiPublic"
                      active-text="公开"
                      inactive-text="关闭"
                    />
                  </el-form-item>

                  <el-form-item label="允许写入" v-if="formData.apiPublic">
                    <el-switch
                      v-model="formData.apiWritable"
                      active-text="允许"
                      inactive-text="只读"
                    />
                    <div style="color: #909399; font-size: 12px; margin-top: 4px">
                      开启后外部系统可通过 Open API 新增和修改数据
                    </div>
                  </el-form-item>

                  <el-form-item label="校验脚本">
                    <el-select
                      v-model="formData.validationScript"
                      clearable
                      placeholder="选择校验脚本（可选）"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="s in allValidationScripts"
                        :key="s.id"
                        :label="s.name"
                        :value="s.id"
                      />
                    </el-select>
                  </el-form-item>

                  <el-form-item>
                    <el-button
                      type="primary"
                      @click="handleSavePageInfo"
                      :loading="saveLoading"
                    >
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 2: 视图配置 -->
              <el-tab-pane label="视图配置" name="view">
                <el-form label-width="100px" class="page-form">
                  <!-- 默认视图选择 -->
                  <el-form-item label="默认视图">
                    <el-select v-model="kanbanDefaultView" placeholder="表格" style="width: 200px">
                      <el-option label="表格视图" value="table" />
                      <el-option label="看板视图" value="kanban" />
                      <el-option label="Excel视图" value="excel" />
                      <el-option label="日历视图" value="calendar" />
                      <el-option label="甘特图" value="gantt" />
                    </el-select>
                  </el-form-item>

                  <!-- 视图类型选择器 -->
                  <el-form-item label="配置视图">
                    <el-radio-group v-model="viewConfigType">
                      <el-radio-button value="kanban">看板</el-radio-button>
                      <el-radio-button value="calendar">日历</el-radio-button>
                      <el-radio-button value="gantt">甘特图</el-radio-button>
                    </el-radio-group>
                  </el-form-item>

                  <!-- 看板视图配置 -->
                  <template v-if="viewConfigType === 'kanban'">
                    <el-divider content-position="left">看板视图</el-divider>

                    <el-form-item label="分组字段">
                      <el-select v-model="kanbanGroupField" clearable placeholder="选择 select 类型字段" style="width: 100%">
                        <el-option
                          v-for="f in selectTypeFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        选择一个下拉选择类型的字段作为看板列的分组依据
                      </div>
                    </el-form-item>

                    <template v-if="kanbanGroupField">
                      <el-form-item label="卡片标题">
                        <el-select v-model="kanbanCardTitle" placeholder="选择标题字段" style="width: 100%">
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="卡片摘要">
                        <el-select v-model="kanbanCardFields" multiple placeholder="选择显示字段" style="width: 100%">
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="颜色字段">
                        <el-select v-model="kanbanColorField" clearable placeholder="可选：按此字段着色" style="width: 100%">
                          <el-option
                            v-for="f in selectTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>
                    </template>
                  </template>

                  <!-- 日历视图配置 -->
                  <template v-if="viewConfigType === 'calendar'">
                    <el-divider content-position="left">日历视图</el-divider>

                    <el-form-item label="日期字段">
                      <el-select v-model="calendarDateField" clearable placeholder="选择 date/datetime 类型字段" style="width: 100%">
                        <el-option
                          v-for="f in dateTypeFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        选择日期字段作为日历视图的时间轴，必须有日期字段才能启用日历视图
                      </div>
                    </el-form-item>

                    <template v-if="calendarDateField">
                      <el-form-item label="结束日期">
                        <el-select v-model="calendarEndDateField" clearable placeholder="可选：支持跨天事件" style="width: 100%">
                          <el-option
                            v-for="f in dateTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <div style="color: #909399; font-size: 12px; margin-top: 4px">
                          选择结束日期字段可支持多天事件，用户可拖拽边缘调整时长
                        </div>
                      </el-form-item>

                      <el-form-item label="卡片标题">
                        <el-select v-model="calendarCardTitle" placeholder="选择标题字段" style="width: 100%">
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="颜色字段">
                        <el-select v-model="calendarColorField" clearable placeholder="可选：按状态字段着色" style="width: 100%">
                          <el-option
                            v-for="f in selectTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="默认模式">
                        <el-radio-group v-model="calendarDefaultMode">
                          <el-radio value="month">月视图</el-radio>
                          <el-radio value="week">周视图</el-radio>
                        </el-radio-group>
                      </el-form-item>
                    </template>
                  </template>

                  <!-- 甘特图视图配置 -->
                  <template v-if="viewConfigType === 'gantt'">
                    <el-divider content-position="left">甘特图</el-divider>

                    <el-form-item label="开始日期">
                      <el-select v-model="ganttStartDateField" clearable placeholder="选择 date/datetime 类型字段" style="width: 100%">
                        <el-option
                          v-for="f in dateTypeFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        选择开始日期字段，必须有开始和结束日期才能启用甘特图
                      </div>
                    </el-form-item>

                    <template v-if="ganttStartDateField">
                      <el-form-item label="结束日期">
                        <el-select v-model="ganttEndDateField" placeholder="选择结束日期字段" style="width: 100%">
                          <el-option
                            v-for="f in dateTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="标题字段">
                        <el-select v-model="ganttTitleField" placeholder="选择任务标题字段" style="width: 100%">
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="进度字段">
                        <el-select v-model="ganttProgressField" clearable placeholder="可选：0-100 数字字段" style="width: 100%">
                          <el-option
                            v-for="f in numberTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="颜色字段">
                        <el-select v-model="ganttColorField" clearable placeholder="可选：按状态字段着色" style="width: 100%">
                          <el-option
                            v-for="f in selectTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                      </el-form-item>

                      <el-form-item label="依赖字段">
                        <el-select v-model="ganttDependenciesField" clearable placeholder="可选：多选字段存储依赖ID" style="width: 100%">
                          <el-option
                            v-for="f in multiSelectTypeFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <div style="color: #909399; font-size: 12px; margin-top: 4px">
                          选择多选字段存储依赖任务ID，用于显示依赖连线
                        </div>
                      </el-form-item>
                    </template>
                  </template>

                  <el-form-item style="margin-top: 16px">
                    <el-button
                      type="primary"
                      @click="handleSavePageInfo"
                      :loading="saveLoading"
                    >
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 3: 字段配置 -->
              <el-tab-pane label="字段配置" name="fields">
                <FieldConfigEditor
                  :page-id="currentPageId!"
                  :fields="currentFields"
                  @update="handleFieldsUpdate"
                  @import="openImportDialog"
                />
              </el-tab-pane>

              <!-- Tab 4: 删除绑定 -->
              <el-tab-pane label="删除绑定" name="deleteBinding">
                <el-form label-width="100px" class="page-form">
                  <el-form-item label="启用删除绑定">
                    <el-switch
                      v-model="deleteBindingEnabled"
                      active-text="启用"
                      inactive-text="关闭"
                    />
                    <div style="color: #909399; font-size: 12px; margin-top: 4px">
                      启用后，删除数据时会弹出表单让用户填写信息，保存后再执行删除
                    </div>
                  </el-form-item>

                  <template v-if="deleteBindingEnabled">
                    <el-form-item label="目标集合" required>
                      <el-select
                        v-model="deleteBindingTargetCollection"
                        placeholder="选择目标集合"
                        style="width: 100%"
                      >
                        <el-option
                          v-for="c in availableTargetCollections"
                          :key="c.id"
                          :label="c.name"
                          :value="c.id"
                        />
                      </el-select>
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        删除记录时，表单数据将保存到此集合
                      </div>
                    </el-form-item>

                    <el-form-item label="对话框标题">
                      <el-input
                        v-model="deleteBindingDialogTitle"
                        placeholder="如：设备报废登记"
                        clearable
                      />
                    </el-form-item>

                    <el-form-item label="对话框宽度">
                      <el-input
                        v-model="deleteBindingDialogWidth"
                        placeholder="500px"
                        style="width: 200px"
                      />
                    </el-form-item>

                    <el-form-item label="自动填充">
                      <el-switch
                        v-model="deleteBindingAutoFillOperator"
                        active-text="操作者信息"
                        inactive-text="关闭"
                      />
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        自动填充操作者用户名、删除时间、源记录ID等信息
                      </div>
                    </el-form-item>

                    <el-divider content-position="left">继承字段映射</el-divider>

                    <div class="inherit-fields-config">
                      <div
                        v-for="(mapping, index) in deleteBindingInheritFields"
                        :key="index"
                        class="inherit-field-item"
                      >
                        <el-select
                          v-model="mapping.sourceField"
                          placeholder="源字段"
                          style="width: 180px"
                        >
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <el-icon class="arrow-icon"><Right /></el-icon>
                        <el-select
                          v-model="mapping.targetField"
                          placeholder="目标字段"
                          style="width: 180px"
                          :disabled="!deleteBindingTargetCollection"
                        >
                          <el-option
                            v-for="f in targetCollectionFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <el-button
                          type="danger"
                          link
                          @click="removeInheritField(index)"
                        >
                          删除
                        </el-button>
                      </div>
                      <el-button type="primary" link @click="addInheritField">
                        + 添加继承字段
                      </el-button>
                    </div>

                    <el-alert type="info" :closable="false" show-icon style="margin-top: 16px">
                      <template #title>
                        表单字段将自动使用目标集合的字段配置，继承字段会自动填充无需用户填写。
                      </template>
                    </el-alert>
                  </template>

                  <el-empty v-else :image-size="60" description="启用删除绑定以配置" />

                  <el-form-item style="margin-top: 20px">
                    <el-button
                      type="primary"
                      @click="handleSaveDeleteBinding"
                      :loading="saveLoading"
                    >
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 5: 关系图谱 -->
              <el-tab-pane label="关系图谱" name="relations">
                <PageConfigRelationGraph
                  :page-id="currentPageId!"
                  @navigate="handleNavigateToPage"
                />
              </el-tab-pane>
            </el-tabs>
          </div>

          <el-empty v-else description="请选择或新增页面配置" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 新增页面对话框 -->
    <el-dialog
      v-model="addDialogVisible"
      title="新增页面配置"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="addFormRef"
        :model="addFormData"
        :rules="formRules"
        label-width="100px"
      >
        <el-form-item label="页面名称" prop="name">
          <el-input
            v-model="addFormData.name"
            placeholder="请输入页面名称"
            maxlength="50"
          />
        </el-form-item>

        <el-form-item label="页面描述" prop="description">
          <el-input
            v-model="addFormData.description"
            type="textarea"
            placeholder="请输入页面描述"
            :rows="2"
          />
        </el-form-item>

        <el-form-item label="API端点" prop="apiEndpoint">
          <el-input
            v-model="addFormData.apiEndpoint"
            placeholder="如：/api/data/my-page"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="createLoading">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 导入字段对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入字段配置"
      width="800px"
      :close-on-click-modal="false"
    >
      <el-tabs v-model="importActiveTab" class="import-tabs">
        <!-- Tab 1: 模板与说明 -->
        <el-tab-pane label="模板与说明" name="template">
          <div class="template-section">
            <h4>下载导入模板</h4>
            <p class="desc">下载 Excel 模板，按照模板格式填写字段配置后上传导入。</p>
            <div class="template-buttons">
              <el-button type="primary" @click="downloadTemplate('xlsx')">
                <el-icon><Download /></el-icon>
                下载 Excel 模板
              </el-button>
              <el-button @click="downloadTemplate('csv')">
                <el-icon><Download /></el-icon>
                下载 CSV 模板
              </el-button>
            </div>

            <el-divider>字段配置说明</el-divider>

            <el-table :data="fieldTypeGuide" size="small" border max-height="400">
              <el-table-column prop="type" label="字段类型" width="110">
                <template #default="{ row }">
                  <el-tag size="small" type="info">{{ row.type }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="label" label="说明" width="160" />
              <el-table-column label="配置示例" min-width="280">
                <template #default="{ row }">
                  <code class="code-example">{{ row.example }}</code>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <!-- Tab 2: 上传文件 -->
        <el-tab-pane label="上传文件" name="upload">
          <el-upload
            ref="uploadRef"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".xlsx,.xls,.csv"
            :on-change="handleFileChange"
          >
            <el-icon class="el-icon--upload"><Upload /></el-icon>
            <div class="el-upload__text">
              将文件拖到此处，或<em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 .xlsx / .xls / .csv 格式，每行一个字段配置
              </div>
            </template>
          </el-upload>

          <!-- 预览区域 -->
          <div v-if="importPreview" class="import-preview">
            <el-divider>解析预览</el-divider>
            <el-descriptions :column="3" border size="small">
              <el-descriptions-item label="解析字段数">{{ importPreview.total }}</el-descriptions-item>
              <el-descriptions-item label="将更新">{{ importPreview.updated }}</el-descriptions-item>
              <el-descriptions-item label="将新增">{{ importPreview.added }}</el-descriptions-item>
            </el-descriptions>
            <el-alert
              v-if="importPreview.errors.length > 0"
              type="warning"
              :closable="false"
              show-icon
              style="margin-top: 12px"
            >
              <template #title>解析警告</template>
              <div v-for="(err, i) in importPreview.errors" :key="i" style="font-size: 12px; margin-top: 4px">
                第 {{ err.row }} 行: {{ err.message }}
              </div>
            </el-alert>

            <el-table :data="importPreview.fields" size="small" max-height="300" style="margin-top: 12px">
              <el-table-column prop="id" label="ID" width="120" />
              <el-table-column prop="fieldName" label="字段名" width="140" />
              <el-table-column prop="label" label="标签" width="140" />
              <el-table-column prop="controlType" label="类型" width="120" />
              <el-table-column prop="required" label="必填" width="60">
                <template #default="{ row }">
                  <el-tag :type="row.required ? 'success' : 'info'" size="small">{{ row.required ? '是' : '否' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="80">
                <template #default="{ row }">
                  <el-tag v-if="row._action === 'update'" type="warning" size="small">更新</el-tag>
                  <el-tag v-else type="success" size="small">新增</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button v-if="importActiveTab === 'upload'" type="primary" @click="handleImportFields" :loading="importLoading" :disabled="!importPreview">
          确认导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * PageConfigManager 组件脚本
 *
 * 功能：
 * 1. 页面配置列表展示
 * 2. 页面配置的增删改
 * 3. 字段配置编辑
 */
import { ref, computed, watch, onMounted, onActivated } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Right, Upload, Download } from '@element-plus/icons-vue'
import { usePageConfigStore } from '@/stores'
import { useMenuStore } from '@/stores'
import FieldConfigEditor from './FieldConfigEditor.vue'
import PageConfigList from './components/PageConfigList.vue'
import PageConfigRelationGraph from '@/components/PageConfigRelationGraph.vue'
import type { PageFormData, FieldConfig, DeleteBindingConfig, InheritFieldMapping } from '@/types'
import type { ExportScript } from '@/types'
import type { ValidationScript } from '@/types'
import { createEmptyPageFormData } from '@/types'
import { getExportScripts } from '@/api/exportScript'
import { getValidationScripts } from '@/api/validationScript'
import { v4 as uuidv4 } from 'uuid'
import * as XLSX from 'xlsx'

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const addFormRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 当前选中的页面ID
 */
const currentPageId = ref<string | null>(null)

/**
 * 当前激活的 Tab
 */
const activeTab = ref('basic')

/**
 * 页面表单数据
 */
const formData = ref<PageFormData>(createEmptyPageFormData())

/**
 * 新增表单数据
 */
const addFormData = ref<PageFormData>(createEmptyPageFormData())

/**
 * 新增对话框可见性
 */
const addDialogVisible = ref(false)

/**
 * 保存加载状态
 */
const saveLoading = ref(false)

/**
 * 创建加载状态
 */
const createLoading = ref(false)

/**
 * 所有导出脚本
 */
const allExportScripts = ref<ExportScript[]>([])

/**
 * 所有校验脚本
 */
const allValidationScripts = ref<ValidationScript[]>([])

/**
 * 视图配置类型选择
 */
const viewConfigType = ref<'kanban' | 'calendar' | 'gantt'>('kanban')

/**
 * 导入字段对话框可见性
 */
const importDialogVisible = ref(false)

/**
 * 导入加载状态
 */
const importLoading = ref(false)

/**
 * 导入预览数据
 */
const importPreview = ref<{
  total: number
  updated: number
  added: number
  fields: Array<Record<string, any>>
  errors: Array<{ row: number; message: string }>
} | null>(null)

/**
 * 待导入的字段数据（确认导入时使用）
 */
const pendingFields = ref<Partial<FieldConfig>[]>([])

/**
 * 导入对话框当前激活的 tab
 */
const importActiveTab = ref('template')

/**
 * 看板配置响应式状态
 */
const kanbanDefaultView = ref<'table' | 'kanban' | 'excel' | 'calendar' | 'gantt'>('table')
const kanbanGroupField = ref('')
const kanbanCardTitle = ref('')
const kanbanCardFields = ref<string[]>([])
const kanbanColorField = ref('')

/**
 * 日历视图配置响应式状态
 */
const calendarDateField = ref('')
const calendarEndDateField = ref('')
const calendarCardTitle = ref('')
const calendarColorField = ref('')
const calendarDefaultMode = ref<'month' | 'week'>('month')

/**
 * 甘特图视图配置响应式状态
 */
const ganttStartDateField = ref('')
const ganttEndDateField = ref('')
const ganttTitleField = ref('')
const ganttProgressField = ref('')
const ganttColorField = ref('')
const ganttDependenciesField = ref('')

/**
 * 删除绑定配置响应式状态
 */
const deleteBindingEnabled = ref(false)
const deleteBindingTargetCollection = ref('')
const deleteBindingDialogTitle = ref('')
const deleteBindingDialogWidth = ref('500px')
const deleteBindingAutoFillOperator = ref(true)
const deleteBindingInheritFields = ref<InheritFieldMapping[]>([])

// ==================== 计算属性（脚本筛选） ====================

const pageExportScripts = computed(() =>
  allExportScripts.value.filter(s => (s.scope || 'page') === 'page')
)

const rowExportScripts = computed(() =>
  allExportScripts.value.filter(s => s.scope === 'row')
)

const selectTypeFields = computed<FieldConfig[]>(() =>
  currentFields.value.filter(f => f.controlType === 'select')
)

const dateTypeFields = computed<FieldConfig[]>(() =>
  currentFields.value.filter(f => f.controlType === 'date' || f.controlType === 'datetime')
)

const numberTypeFields = computed<FieldConfig[]>(() =>
  currentFields.value.filter(f => f.controlType === 'number')
)

const multiSelectTypeFields = computed<FieldConfig[]>(() =>
  currentFields.value.filter(f => f.controlType === 'multiSelect')
)

// ==================== 常量 ====================

/**
 * 表单验证规则
 */
const formRules: FormRules = {
  name: [
    { required: true, message: '请输入页面名称', trigger: 'blur' },
    { min: 2, max: 50, message: '长度在 2 到 50 个字符', trigger: 'blur' }
  ],
  apiEndpoint: [
    { required: true, message: '请输入API端点', trigger: 'blur' }
  ]
}

/**
 * 字段类型配置说明表
 */
const fieldTypeGuide = [
  { type: 'text', label: '单行文本', example: 'fieldName: name, label: 名称' },
  { type: 'textarea', label: '多行文本', example: 'controlType: textarea' },
  { type: 'number', label: '数字', example: 'controlType: number, min: 0, max: 100' },
  { type: 'select', label: '下拉选择', example: 'options: [{"label":"A","value":"a"}]' },
  { type: 'multiSelect', label: '多选', example: 'options: [{"label":"A","value":"a"}]' },
  { type: 'radio', label: '单选按钮', example: 'options: [{"label":"A","value":"a"}]' },
  { type: 'checkbox', label: '复选框', example: 'controlType: checkbox' },
  { type: 'date', label: '日期', example: 'controlType: date' },
  { type: 'datetime', label: '日期时间', example: 'controlType: datetime' },
  { type: 'file', label: '文件上传', example: 'controlType: file' },
  { type: 'image', label: '图片上传', example: 'controlType: image' },
  {
    type: 'relation',
    label: '多对多双向关联',
    example: '{"targetCollection":"test-products","displayField":"productName","targetField":"relatedCases"}'
  },
  {
    type: 'reference',
    label: '一对多引用+继承',
    example: '{"targetCollection":"test-templates","displayField":"templateName","inheritFields":["description","category"]}'
  },
  {
    type: 'quoteSelect',
    label: '单向多选引用',
    example: '{"targetCollection":"test-cases","displayField":"name"}'
  },
  { type: 'autoSequence', label: '自动编号', example: '{"prefix":"IC-","max":999}' },
  { type: 'autoTimestamp', label: '自动时间戳', example: 'controlType: autoTimestamp' },
]

// ==================== 计算属性 ====================

/**
 * 页面配置列表
 */
const pageConfigs = computed(() => pageConfigStore.pageConfigs)

/**
 * 当前页面配置
 */
const currentPageConfig = computed(() => {
  if (!currentPageId.value) return null
  return pageConfigStore.getPageConfigById(currentPageId.value)
})

/**
 * 当前页面字段列表
 */
const currentFields = computed(() => {
  return currentPageConfig.value?.fields || []
})

/**
 * 是否显示详情
 */
const showDetail = computed(() => currentPageId.value !== null)

/**
 * 详情标题
 */
const detailTitle = computed(() => {
  return currentPageConfig.value?.name || '页面详情'
})

/**
 * 可选的目标集合列表（排除当前页面）
 */
const availableTargetCollections = computed(() => {
  return pageConfigs.value
    .filter(p => p.id !== currentPageId.value)
    .map(p => ({
      id: p.id.replace('page-', ''),
      name: p.name
    }))
})

/**
 * 目标集合的字段列表
 */
const targetCollectionFields = computed<FieldConfig[]>(() => {
  if (!deleteBindingTargetCollection.value) return []
  const targetPageId = `page-${deleteBindingTargetCollection.value}`
  const targetConfig = pageConfigStore.getPageConfigById(targetPageId)
  return targetConfig?.fields || []
})

// ==================== 方法 ====================

/**
 * 根据 pageId 加载表单数据
 *
 * 由 watch(currentPageId) 触发，currentPageId 由左侧 PageConfigList 的 v-model 驱动。
 */
function loadFormForPage(id: string): void {
  const config = pageConfigStore.getPageConfigById(id)
  if (!config) return
  formData.value = {
    id: config.id,
    name: config.name,
    description: config.description || '',
    apiEndpoint: config.apiEndpoint,
    exportScripts: config.exportScripts || [],
    rowExportScripts: config.rowExportScripts || [],
    apiPublic: config.apiPublic || false,
    apiWritable: config.apiWritable || false,
    validationScript: config.validationScript || '',
    viewConfig: config.viewConfig || {},
  }
  // Load kanban config
  const vc = config.viewConfig || {}
  kanbanDefaultView.value = vc.defaultView || 'table'
  // Initialize viewConfigType based on defaultView
  if (vc.defaultView === 'calendar') {
    viewConfigType.value = 'calendar'
  } else if (vc.defaultView === 'gantt') {
    viewConfigType.value = 'gantt'
  } else {
    viewConfigType.value = 'kanban'
  }
  kanbanGroupField.value = vc.kanban?.groupField || ''
  kanbanCardTitle.value = vc.kanban?.cardTitle || ''
  kanbanCardFields.value = vc.kanban?.cardFields || []
  kanbanColorField.value = vc.kanban?.cardColorField || ''
  // Load calendar config
  calendarDateField.value = vc.calendar?.dateField || ''
  calendarEndDateField.value = vc.calendar?.endDateField || ''
  calendarCardTitle.value = vc.calendar?.cardTitle || ''
  calendarColorField.value = vc.calendar?.cardColorField || ''
  calendarDefaultMode.value = vc.calendar?.defaultMode || 'month'
  // Load gantt config
  ganttStartDateField.value = vc.gantt?.startDateField || ''
  ganttEndDateField.value = vc.gantt?.endDateField || ''
  ganttTitleField.value = vc.gantt?.titleField || ''
  ganttProgressField.value = vc.gantt?.progressField || ''
  ganttColorField.value = vc.gantt?.colorField || ''
  ganttDependenciesField.value = vc.gantt?.dependenciesField || ''
  // Load delete binding config
  const db = config.deleteBinding
  deleteBindingEnabled.value = db?.enabled || false
  deleteBindingTargetCollection.value = db?.targetCollection || ''
  deleteBindingDialogTitle.value = db?.dialogTitle || ''
  deleteBindingDialogWidth.value = db?.dialogWidth || '500px'
  deleteBindingAutoFillOperator.value = db?.autoFillOperator ?? true
  deleteBindingInheritFields.value = db?.inheritFields ? [...db.inheritFields] : []
}

watch(currentPageId, (id) => {
  if (id) loadFormForPage(id)
})

/**
 * 处理新增
 */
function handleAdd(): void {
  addFormData.value = createEmptyPageFormData()
  addDialogVisible.value = true
}

/**
 * 处理创建
 */
async function handleCreate(): Promise<void> {
  const valid = await addFormRef.value?.validate()
  if (!valid) return

  createLoading.value = true
  try {
    const created = await pageConfigStore.addPageConfig({
      name: addFormData.value.name,
      description: addFormData.value.description,
      apiEndpoint: addFormData.value.apiEndpoint,
      fields: []
    })
    ElMessage.success('创建成功')
    addDialogVisible.value = false
    // 选中新创建的页面（watch 会自动 setup 表单）
    currentPageId.value = created.id
  } catch (error) {
    ElMessage.error('创建失败')
  } finally {
    createLoading.value = false
  }
}

/**
 * 处理保存页面基本信息
 */
async function handleSavePageInfo(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid || !currentPageId.value) return

  // Build viewConfig from kanban state
  const viewConfig: Record<string, any> = {
    defaultView: kanbanDefaultView.value,
  }
  if (kanbanGroupField.value) {
    viewConfig.kanban = {
      groupField: kanbanGroupField.value,
      cardTitle: kanbanCardTitle.value,
      cardFields: kanbanCardFields.value,
      cardColorField: kanbanColorField.value || undefined,
    }
  }
  // Build calendar config
  if (calendarDateField.value) {
    viewConfig.calendar = {
      dateField: calendarDateField.value,
      endDateField: calendarEndDateField.value || undefined,
      cardTitle: calendarCardTitle.value,
      cardColorField: calendarColorField.value || undefined,
      defaultMode: calendarDefaultMode.value,
    }
  }

  // Build gantt config
  if (ganttStartDateField.value && ganttEndDateField.value) {
    viewConfig.gantt = {
      startDateField: ganttStartDateField.value,
      endDateField: ganttEndDateField.value,
      titleField: ganttTitleField.value,
      progressField: ganttProgressField.value || undefined,
      colorField: ganttColorField.value || undefined,
      dependenciesField: ganttDependenciesField.value || undefined,
    }
  }

  // Build deleteBinding config
  let deleteBinding: DeleteBindingConfig | undefined
  if (deleteBindingEnabled.value && deleteBindingTargetCollection.value) {
    deleteBinding = {
      enabled: true,
      targetCollection: deleteBindingTargetCollection.value,
      dialogTitle: deleteBindingDialogTitle.value || undefined,
      dialogWidth: deleteBindingDialogWidth.value || '500px',
      autoFillOperator: deleteBindingAutoFillOperator.value,
      inheritFields: deleteBindingInheritFields.value,
      fields: [],
    }
  }

  saveLoading.value = true
  try {
    await pageConfigStore.updatePageConfig(currentPageId.value, {
      name: formData.value.name,
      description: formData.value.description,
      apiEndpoint: formData.value.apiEndpoint,
      exportScripts: formData.value.exportScripts || [],
      rowExportScripts: formData.value.rowExportScripts || [],
      apiPublic: formData.value.apiPublic,
      apiWritable: formData.value.apiPublic ? formData.value.apiWritable : false,
      validationScript: formData.value.validationScript || undefined,
      viewConfig,
      deleteBinding,
      fields: currentFields.value
    })
    ElMessage.success('保存成功')
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

/**
 * 处理字段配置更新
 */
async function handleFieldsUpdate(fields: FieldConfig[]): Promise<void> {
  if (!currentPageId.value) return

  try {
    await pageConfigStore.updatePageFields(currentPageId.value, fields)
    ElMessage.success('字段配置已更新')
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

// ==================== 导入字段相关 ====================

const COMPLEX_FIELD_KEYS = [
  'options', 'relationConfig', 'referenceConfig', 'quoteConfig',
  'sequenceConfig', 'workflowConfig', 'optionsSource'
] as const

const BOOL_FIELD_KEYS = [
  'required', 'isPrimaryKey', 'hidden', 'disabled', 'readonly'
] as const

const NUMBER_FIELD_KEYS = ['order', 'min', 'max'] as const

/**
 * 下载导入模板
 */
function downloadTemplate(format: 'xlsx' | 'csv'): void {
  const templateData = [
    { id: 'field-1', fieldName: 'name', label: '名称', controlType: 'text', required: true, order: 1, placeholder: '请输入名称', isPrimaryKey: true },
    { id: 'field-2', fieldName: 'status', label: '状态', controlType: 'select', required: true, order: 2, options: JSON.stringify([{ label: '开启', value: '1' }, { label: '关闭', value: '0' }]) },
    { fieldName: 'description', label: '描述', controlType: 'textarea', required: false, order: 3, placeholder: '请输入描述' },
    { fieldName: 'priority', label: '优先级', controlType: 'radio', required: false, order: 4, options: JSON.stringify([{ label: '高', value: 'high' }, { label: '中', value: 'medium' }, { label: '低', value: 'low' }]) },
    { fieldName: 'startDate', label: '开始日期', controlType: 'date', required: false, order: 5 },
    { fieldName: 'version', label: '版本号', controlType: 'number', required: false, order: 6, min: 1, max: 99 },
    { fieldName: 'attachment', label: '附件', controlType: 'file', required: false, order: 7 },
    { fieldName: 'tags', label: '标签', controlType: 'multiSelect', required: false, order: 8, options: JSON.stringify([{ label: '标签A', value: 'a' }, { label: '标签B', value: 'b' }]) },
    { fieldName: 'caseNo', label: '编号', controlType: 'autoSequence', required: false, order: 9, sequenceConfig: JSON.stringify({ prefix: 'TC-', max: 9999 }) },
    { fieldName: 'createdAt', label: '创建时间', controlType: 'autoTimestamp', required: false, order: 10, hidden: true },
    // 数据关联：多对多双向，需配置目标集合、显示字段、反向关联字段名
    { fieldName: 'relatedProduct', label: '关联产品', controlType: 'relation', required: false, order: 11, relationConfig: JSON.stringify({ targetCollection: 'test-products', displayField: 'productName', targetField: 'relatedCases' }) },
    // 数据引用：一对多引用+字段继承，需配置目标集合、显示字段、继承字段列表
    { fieldName: 'template', label: '测试模板', controlType: 'reference', required: false, order: 12, referenceConfig: JSON.stringify({ targetCollection: 'test-templates', displayField: 'templateName', inheritFields: ['description', 'category'] }) },
    // 引用选择：单向多选引用，只需配置目标集合和显示字段
    { fieldName: 'relatedCases', label: '引用用例', controlType: 'quoteSelect', required: false, order: 13, quoteConfig: JSON.stringify({ targetCollection: 'test-cases', displayField: 'name' }) },
  ]

  if (format === 'csv') {
    const headers = ['id', 'fieldName', 'label', 'controlType', 'required', 'order', 'placeholder', 'isPrimaryKey', 'min', 'max', 'hidden', 'options', 'relationConfig', 'referenceConfig', 'quoteConfig', 'sequenceConfig']
    const rows = [
      headers.join(','),
      ...templateData.map(d => headers.map(h => {
        const v = (d as any)[h]
        if (v === undefined || v === null || v === '') return ''
        if (typeof v === 'string' && (v.includes(',') || v.includes('"'))) return '"' + v.replace(/"/g, '""') + '"'
        return v
      }).join(','))
    ]
    const blob = new Blob(['﻿' + rows.join('\n')], { type: 'text/csv;charset=utf-8' })
    triggerDownload(blob, '字段导入模板.csv')
  } else {
    const ws = XLSX.utils.json_to_sheet(templateData)
    ws['!cols'] = [
      { wch: 12 }, { wch: 18 }, { wch: 12 }, { wch: 14 }, { wch: 8 }, { wch: 6 },
      { wch: 18 }, { wch: 10 }, { wch: 6 }, { wch: 6 }, { wch: 6 }, { wch: 50 },
      { wch: 50 }, { wch: 50 }, { wch: 50 }, { wch: 40 }
    ]
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, '字段配置')
    XLSX.writeFile(wb, '字段导入模板.xlsx')
  }
}

/**
 * 触发浏览器下载
 */
function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * 打开导入对话框
 */
function openImportDialog(): void {
  if (!currentPageId.value) {
    ElMessage.warning('请先选择一个页面配置')
    return
  }
  importDialogVisible.value = true
  importPreview.value = null
  pendingFields.value = []
}

/**
 * 处理文件选择
 */
function handleFileChange(file: any): void {
  const rawFile = file.raw
  if (!rawFile) return

  const reader = new FileReader()
  reader.onload = (e) => {
    try {
      const data = e.target?.result
      const workbook = XLSX.read(data, { type: 'array' })
      const sheetName = workbook.SheetNames[0]
      const worksheet = workbook.Sheets[sheetName]
      const rows = XLSX.utils.sheet_to_json<Record<string, any>>(worksheet)

      if (rows.length === 0) {
        ElMessage.warning('文件中没有数据')
        return
      }

      parseAndPreview(rows)
    } catch (error: any) {
      ElMessage.error('文件解析失败: ' + (error.message || '未知错误'))
    }
  }
  reader.readAsArrayBuffer(rawFile)
}

/**
 * 解析 Excel 行数据为字段配置
 */
function parseFieldRow(row: Record<string, any>): Partial<FieldConfig> {
  const field: Partial<FieldConfig> = {}
  for (const [key, value] of Object.entries(row)) {
    if (value === undefined || value === null || value === '') continue

    if ((COMPLEX_FIELD_KEYS as readonly string[]).includes(key)) {
      try {
        (field as any)[key] = typeof value === 'string' ? JSON.parse(value) : value
      } catch {
        (field as any)[key] = value
      }
    } else if ((BOOL_FIELD_KEYS as readonly string[]).includes(key)) {
      (field as any)[key] = value === 'true' || value === true || value === 1 || value === '1'
    } else if ((NUMBER_FIELD_KEYS as readonly string[]).includes(key)) {
      (field as any)[key] = Number(value) || 0
    } else {
      (field as any)[key] = value
    }
  }
  return field
}

/**
 * 合并导入字段到现有字段中
 */
function mergeFields(existing: FieldConfig[], imported: Partial<FieldConfig>[]): {
  merged: FieldConfig[]
  updated: number
  added: number
} {
  const result: FieldConfig[] = [...existing]
  let updated = 0
  let added = 0

  for (const imp of imported) {
    if (!imp.id) {
      // 无 id → 生成新 id 追加
      const newField = { ...imp, id: `field-${uuidv4().slice(0, 8)}` } as FieldConfig
      result.push(newField)
      added++
    } else {
      const idx = result.findIndex(f => f.id === imp.id)
      if (idx >= 0) {
        // 已存在 → 合并更新
        result[idx] = { ...result[idx], ...imp }
        updated++
      } else {
        // 不存在 → 追加
        result.push(imp as FieldConfig)
        added++
      }
    }
  }

  // 按 order 排序
  result.sort((a, b) => (a.order || 0) - (b.order || 0))
  return { merged: result, updated, added }
}

/**
 * 解析并预览导入数据
 */
function parseAndPreview(rows: Record<string, any>[]): void {
  const errors: Array<{ row: number; message: string }> = []
  const parsed: Partial<FieldConfig>[] = []

  rows.forEach((row, index) => {
    const field = parseFieldRow(row)
    if (!field.fieldName) {
      errors.push({ row: index + 2, message: '缺少 fieldName 列' })
      return
    }
    if (!field.label) {
      errors.push({ row: index + 2, message: '缺少 label 列' })
      return
    }
    if (!field.controlType) {
      errors.push({ row: index + 2, message: '缺少 controlType 列' })
      return
    }
    // 确保有 order
    if (!field.order) {
      field.order = index + 1
    }
    parsed.push(field)
  })

  pendingFields.value = parsed

  // 合并预览
  const existing = currentFields.value
  const { updated, added } = mergeFields(existing, parsed)

  // 构建预览表格数据
  const previewFields = parsed.map(f => {
    const exists = f.id ? existing.some(e => e.id === f.id) : false
    return {
      ...f,
      _action: exists ? 'update' : 'add'
    }
  })

  importPreview.value = {
    total: parsed.length,
    updated,
    added,
    fields: previewFields,
    errors
  }
}

/**
 * 确认导入字段
 */
async function handleImportFields(): Promise<void> {
  if (!currentPageId.value || pendingFields.value.length === 0) return

  importLoading.value = true
  try {
    const { merged, updated, added } = mergeFields(currentFields.value, pendingFields.value)
    await pageConfigStore.updatePageFields(currentPageId.value, merged)
    ElMessage.success(`导入完成：更新 ${updated} 个，新增 ${added} 个`)
    importDialogVisible.value = false
    importPreview.value = null
    pendingFields.value = []
  } catch (error: any) {
    ElMessage.error('导入失败: ' + (error.response?.data?.error || error.message || '未知错误'))
  } finally {
    importLoading.value = false
  }
}

/**
 * 添加继承字段映射
 */
function addInheritField(): void {
  deleteBindingInheritFields.value.push({
    sourceField: '',
    targetField: ''
  })
}

/**
 * 删除继承字段映射
 */
function removeInheritField(index: number): void {
  deleteBindingInheritFields.value.splice(index, 1)
}

/**
 * 处理保存删除绑定配置
 */
async function handleSaveDeleteBinding(): Promise<void> {
  if (!currentPageId.value) return

  // Build deleteBinding config
  let deleteBinding: DeleteBindingConfig | undefined
  if (deleteBindingEnabled.value && deleteBindingTargetCollection.value) {
    deleteBinding = {
      enabled: true,
      targetCollection: deleteBindingTargetCollection.value,
      dialogTitle: deleteBindingDialogTitle.value || undefined,
      dialogWidth: deleteBindingDialogWidth.value || '500px',
      autoFillOperator: deleteBindingAutoFillOperator.value,
      inheritFields: deleteBindingInheritFields.value,
      fields: [], // 不再需要单独配置，表单字段自动从目标集合获取
    }
  }

  saveLoading.value = true
  try {
    // 获取当前页面配置
    const currentConfig = currentPageConfig.value
    if (!currentConfig) return

    await pageConfigStore.updatePageConfig(currentPageId.value, {
      name: currentConfig.name,
      description: currentConfig.description || '',
      apiEndpoint: currentConfig.apiEndpoint,
      exportScripts: currentConfig.exportScripts || [],
      rowExportScripts: currentConfig.rowExportScripts || [],
      apiPublic: currentConfig.apiPublic || false,
      apiWritable: currentConfig.apiWritable || false,
      validationScript: currentConfig.validationScript || undefined,
      viewConfig: currentConfig.viewConfig || {},
      deleteBinding,
      fields: currentConfig.fields
    })
    ElMessage.success('删除绑定配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

/**
 * 处理导航到目标页面配置
 */
function handleNavigateToPage(targetPageId: string): void {
  const targetConfig = pageConfigStore.getPageConfigById(targetPageId)

  if (targetConfig) {
    currentPageId.value = targetPageId
  } else {
    ElMessage.warning('目标页面配置不存在')
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
  // 确保菜单数据已加载（左侧列表的项目反查依赖此数据）
  if (menuStore.menuList.length === 0) {
    try {
      await menuStore.fetchMenus()
    } catch {
      // 菜单加载失败时左侧仍能展示，只是项目分组为空
    }
  }
})

onActivated(async () => {
  try {
    allExportScripts.value = await getExportScripts()
  } catch {
    // ignore
  }
  try {
    allValidationScripts.value = await getValidationScripts()
  } catch {
    // ignore
  }
})
</script>

<style scoped lang="scss">
.page-config-manager {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.full-height {
  height: 100%;
  flex: 1;

  // 确保 el-col 继承高度
  > .el-col {
    height: 100%;
  }
}

// 使用 flex 布局让 card 内容正确滚动
.list-card,
.detail-card {
  height: 100%;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    flex: 1;
    overflow: auto;
    min-height: 0; // 关键：允许 flex 子项收缩并滚动
  }
}

.list-card {
  :deep(.el-card__body) {
    padding: 12px;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

// 页面详情 - 占满 card body，内部滚动由 card body 处理
.page-detail {
  height: 100%;
}

// Element Plus border-card tabs - 去掉默认样式并启用滚动
.page-detail :deep(.el-tabs--border-card) {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
  border: none;
  box-shadow: none;

  > .el-tabs__header {
    flex-shrink: 0;
    background-color: var(--el-fill-color-light);
    border-bottom: 1px solid var(--el-border-color-light);
  }

  > .el-tabs__content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 16px;
  }

  > .el-tabs__content > .el-tab-pane {
    height: 100%;
  }
}

.page-form {
  max-width: 600px;
}

.import-preview {
  margin-top: 16px;
}

.template-section {
  h4 {
    margin: 0 0 8px;
    font-size: 15px;
  }

  .desc {
    color: #606266;
    font-size: 13px;
    margin: 0 0 16px;
  }

  .template-buttons {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
  }
}

.code-example {
  font-size: 12px;
  color: #409eff;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  word-break: break-all;
}

.import-tabs :deep(.el-tabs__content) {
  padding-top: 16px;
  min-height: 300px;
}

.inherit-fields-config {
  margin-bottom: 16px;

  .inherit-field-item {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    padding: 8px;
    background-color: #f5f7fa;
    border-radius: 4px;

    .arrow-icon {
      color: #909399;
    }
  }
}
</style>
