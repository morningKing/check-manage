/**
 * 字段配置编辑器组件
 *
 * 职责：
 * - 可视化编辑页面字段配置
 * - 支持字段的增删改
 * - 支持拖拽排序
 * - 配置字段属性（名称、类型、必填等）
 */
<template>
  <div class="field-config-editor">
    <!-- 锁定提示:页面已有数据时 -->
    <el-alert
      v-if="lockExistingFields"
      type="warning"
      :closable="false"
      show-icon
      title="该页面已存在数据"
      description="已有字段被锁定,只能新增字段。重命名/改类型/删除现有字段会让旧数据语义错位,因此被禁止。"
      style="margin-bottom: 12px"
    />

    <!-- 工具栏 -->
    <div class="editor-toolbar">
      <el-button type="primary" size="small" @click="handleAddField">
        <el-icon><Plus /></el-icon>
        添加字段
      </el-button>
      <el-button size="small" @click="$emit('import')">
        <el-icon><Upload /></el-icon>
        导入字段
      </el-button>
      <el-button size="small" @click="handleSaveAll" :loading="saving">
        <el-icon><Check /></el-icon>
        保存配置
      </el-button>
    </div>

    <!-- 字段列表 -->
    <div class="field-list" v-if="localFields.length > 0">
      <draggable
        v-model="localFields"
        item-key="id"
        handle=".drag-handle"
        @end="handleDragEnd"
      >
        <template #item="{ element, index }">
          <div class="field-item">
            <!-- 拖拽手柄 -->
            <div class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>

            <!-- 字段信息 -->
            <div class="field-content">
              <div class="field-main">
                <span class="field-label">{{ element.label }}</span>
                <el-tag size="small" type="info">
                  {{ getControlTypeLabel(element.controlType) }}
                </el-tag>
                <el-tag v-if="element.required" size="small" type="danger">
                  必填
                </el-tag>
                <el-tag v-if="element.isPrimaryKey" size="small" type="warning">
                  主键
                </el-tag>
                <el-tag
                  v-if="element.indexed && fieldIndexStatuses[element.fieldName]"
                  size="small"
                  :type="fieldIndexStatusTagType(fieldIndexStatuses[element.fieldName].status)"
                >
                  索引 · {{ fieldIndexStatusLabel(fieldIndexStatuses[element.fieldName].status) }}
                </el-tag>
              </div>
              <div class="field-meta">
                字段名: {{ element.fieldName }}
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="field-actions">
              <el-tooltip
                v-if="isLockedField(element.fieldName)"
                content="该页面已存在数据,已有字段不可修改"
                placement="top"
              >
                <el-tag size="small" type="info">已锁定</el-tag>
              </el-tooltip>
              <el-button
                v-else
                type="primary" link
                @click="handleEditField(element, index)"
              >
                编辑
              </el-button>
              <el-button
                v-if="!isLockedField(element.fieldName)"
                type="danger" link
                @click="handleDeleteField(index)"
              >
                删除
              </el-button>
            </div>
          </div>
        </template>
      </draggable>
    </div>

    <el-empty v-else description="暂无字段配置，点击上方按钮添加" />

    <!-- 字段编辑对话框 -->
    <el-dialog
      v-model="editDialogVisible"
      :title="editDialogTitle"
      width="650px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form
        ref="fieldFormRef"
        :model="fieldFormData"
        :rules="fieldFormRules"
        label-width="100px"
      >
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="显示名称" prop="label">
              <el-input
                v-model="fieldFormData.label"
                placeholder="请输入字段显示名称"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="字段名" prop="fieldName">
              <el-input
                v-model="fieldFormData.fieldName"
                placeholder="后端字段名，如 userName"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="控件类型" prop="controlType">
              <el-select
                v-model="fieldFormData.controlType"
                placeholder="请选择控件类型"
                style="width: 100%"
              >
                <el-option
                  v-for="type in controlTypeOptions"
                  :key="type.value"
                  :label="type.label"
                  :value="type.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="必填" prop="required">
              <el-switch v-model="fieldFormData.required" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="主键" prop="isPrimaryKey">
              <el-switch v-model="fieldFormData.isPrimaryKey" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-if="showIndexedToggle" label="加速筛选/排序">
          <div class="indexed-toggle-row">
            <el-switch v-model="fieldFormData.indexed" />
            <span class="indexed-hint">
              后台异步为该字段建索引，用于加快按此字段筛选/排序；数据量大的页面建索引可能需要几分钟
            </span>
            <el-tag
              v-if="editingFieldIndexStatus"
              size="small"
              :type="fieldIndexStatusTagType(editingFieldIndexStatus.status)"
            >
              {{ fieldIndexStatusLabel(editingFieldIndexStatus.status) }}
            </el-tag>
          </div>
          <div v-if="editingFieldIndexStatus?.status === 'failed' && editingFieldIndexStatus.error" class="indexed-error">
            建索引失败：{{ editingFieldIndexStatus.error }}
          </div>
        </el-form-item>

        <el-form-item label="占位提示" prop="placeholder">
          <el-input
            v-model="fieldFormData.placeholder"
            placeholder="输入框的提示文字"
          />
        </el-form-item>

        <!-- 选项配置（仅下拉、单选、多选显示） -->
        <el-form-item
          v-if="showOptionsConfig"
          label="选项配置"
        >
          <div class="options-config">
            <el-radio-group
              v-model="fieldFormData.optionsSource.type"
              class="options-type"
            >
              <el-radio value="static">静态选项</el-radio>
              <el-radio value="api">API获取</el-radio>
              <el-radio value="collection">数据页数据</el-radio>
            </el-radio-group>

            <!-- 静态选项配置 -->
            <div v-if="fieldFormData.optionsSource.type === 'static'" class="static-options">
              <div
                v-for="(option, optIndex) in fieldFormData.options"
                :key="optIndex"
                class="option-row"
              >
                <el-input
                  v-model="option.label"
                  placeholder="显示文本"
                  style="width: 45%"
                />
                <el-input
                  v-model="option.value"
                  placeholder="值"
                  style="width: 45%"
                />
                <el-button
                  type="danger"
                  link
                  @click="removeOption(optIndex)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
              <div class="option-actions">
                <el-button type="primary" link @click="addOption">
                  <el-icon><Plus /></el-icon>
                  添加选项
                </el-button>
                <el-button type="primary" link @click="batchDialogVisible = true">
                  <el-icon><DocumentAdd /></el-icon>
                  批量添加
                </el-button>
              </div>

              <!-- 批量添加对话框 -->
              <el-dialog
                v-model="batchDialogVisible"
                title="批量添加选项"
                width="480px"
                append-to-body
              >
                <p class="batch-hint">每行一个选项。可用 <code>:</code> 分隔显示文本和值，省略则两者相同。</p>
                <el-input
                  v-model="batchOptionsText"
                  type="textarea"
                  :rows="8"
                  placeholder="选项A&#10;选项B:value_b&#10;选项C"
                />
                <template #footer>
                  <el-button @click="batchDialogVisible = false">取消</el-button>
                  <el-button type="primary" @click="confirmBatchAdd">确定添加</el-button>
                </template>
              </el-dialog>
            </div>

            <!-- API选项配置 -->
            <div v-else-if="fieldFormData.optionsSource.type === 'api'" class="api-options">
              <el-form-item label="API地址" label-width="80px">
                <el-input
                  v-model="fieldFormData.optionsSource.url"
                  placeholder="如：/api/options/status"
                />
              </el-form-item>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="标签字段" label-width="80px">
                    <el-input
                      v-model="fieldFormData.optionsSource.labelField"
                      placeholder="label"
                    />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="值字段" label-width="80px">
                    <el-input
                      v-model="fieldFormData.optionsSource.valueField"
                      placeholder="value"
                    />
                  </el-form-item>
                </el-col>
              </el-row>
            </div>

            <!-- 数据页数据选项配置 -->
            <div v-else-if="fieldFormData.optionsSource.type === 'collection'" class="collection-options">
              <el-form-item label="数据页" label-width="80px">
                <el-select
                  v-model="fieldFormData.optionsSource.collection"
                  placeholder="请选择数据页"
                  filterable
                  style="width: 100%"
                  @change="handleOptionsCollectionChange"
                >
                  <el-option
                    v-for="opt in optionsCollectionList"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
              </el-form-item>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="标签字段" label-width="80px">
                    <el-select
                      v-model="fieldFormData.optionsSource.labelField"
                      placeholder="选择显示字段"
                      filterable
                      style="width: 100%"
                      :disabled="!fieldFormData.optionsSource.collection"
                    >
                      <el-option
                        v-for="opt in optionsCollectionFieldList"
                        :key="opt.value"
                        :label="`${opt.label}（${opt.value}）`"
                        :value="opt.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="值字段" label-width="80px">
                    <el-select
                      v-model="fieldFormData.optionsSource.valueField"
                      placeholder="选择值字段"
                      filterable
                      style="width: 100%"
                      :disabled="!fieldFormData.optionsSource.collection"
                    >
                      <el-option
                        v-for="opt in optionsCollectionFieldList"
                        :key="opt.value"
                        :label="`${opt.label}（${opt.value}）`"
                        :value="opt.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
            </div>
          </div>
        </el-form-item>

        <!-- 默认值（输入 / 选择类控件，新增记录时预填） -->
        <el-form-item v-if="showDefaultValue" label="默认值">
          <!-- 单选下拉 / 单选按钮：从已配置的静态选项中选 -->
          <el-select
            v-if="['select', 'radio'].includes(fieldFormData.controlType) && fieldFormData.optionsSource.type === 'static'"
            v-model="fieldFormData.defaultValue"
            placeholder="选择默认选项"
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="(opt, i) in fieldFormData.options"
              :key="i"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <!-- 多选下拉 / 复选框：多选静态选项 -->
          <el-select
            v-else-if="['multiSelect', 'checkbox'].includes(fieldFormData.controlType) && fieldFormData.optionsSource.type === 'static'"
            v-model="fieldFormData.defaultValue"
            multiple
            placeholder="选择默认选项"
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="(opt, i) in fieldFormData.options"
              :key="i"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <!-- 数字 -->
          <el-input-number
            v-else-if="fieldFormData.controlType === 'number'"
            v-model="fieldFormData.defaultValue"
            controls-position="right"
            style="width: 100%"
          />
          <!-- 日期 / 日期时间 -->
          <el-date-picker
            v-else-if="['date', 'datetime'].includes(fieldFormData.controlType)"
            v-model="fieldFormData.defaultValue"
            :type="fieldFormData.controlType === 'date' ? 'date' : 'datetime'"
            :value-format="fieldFormData.controlType === 'date' ? 'YYYY-MM-DD' : 'YYYY-MM-DDTHH:mm:ss'"
            placeholder="选择默认日期"
            clearable
            style="width: 100%"
          />
          <!-- 其它（文本 / 多行文本，或来源为 API / 数据页的选择类）：直接输入 -->
          <el-input
            v-else
            v-model="fieldFormData.defaultValue"
            placeholder="输入默认值（留空表示无）"
            clearable
          />
          <div class="default-value-tip">新增记录时预填该值；留空表示无默认值。</div>
        </el-form-item>

        <!-- 关联配置（仅关联类型显示） -->
        <el-form-item
          v-if="showRelationConfig"
          label="关联配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.displayField"
                placeholder="请选择显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.relationConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in displayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="反向字段" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.targetField"
                placeholder="请选择或输入反向字段名"
                filterable
                allow-create
                style="width: 100%"
                :disabled="!fieldFormData.relationConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in targetFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>

        <!-- 引用配置（仅引用类型显示） -->
        <el-form-item
          v-if="showReferenceConfig"
          label="引用配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleRefTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.displayField"
                placeholder="请选择下拉显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.referenceConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in refDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="继承字段" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.inheritFields"
                placeholder="选择需要在表格中显示的父字段"
                filterable
                multiple
                style="width: 100%"
                :disabled="!fieldFormData.referenceConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in refDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>

        <!-- 序列配置（仅自增序列类型显示） -->
        <el-form-item
          v-if="showSequenceConfig"
          label="序列配置"
        >
          <div class="sequence-config">
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="前缀" label-width="60px">
                  <el-input
                    v-model="fieldFormData.sequenceConfig!.prefix"
                    placeholder="如 IC-"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="上限" label-width="60px">
                  <el-input-number
                    v-model="fieldFormData.sequenceConfig!.max"
                    :min="1"
                    :max="999999"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>
            <div v-if="sequencePreview" class="sequence-preview">
              格式预览：{{ sequencePreview }}
            </div>
          </div>
        </el-form-item>

        <!-- 组合文本配置（仅组合文本类型显示） -->
        <el-form-item
          v-if="showCompositeTextConfig"
          label="组合配置"
        >
          <div class="composite-text-config">
            <el-form-item label="源字段" label-width="80px">
              <el-select
                v-model="fieldFormData.compositeTextConfig!.sourceFields"
                multiple
                placeholder="选择要拼接的文本字段"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="opt in compositeSourceFieldOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="分隔符" label-width="80px">
              <el-input
                v-model="fieldFormData.compositeTextConfig!.separator"
                placeholder="如： - "
              />
            </el-form-item>
            <div v-if="compositeTextPreview" class="sequence-preview">
              预览：{{ compositeTextPreview }}
            </div>
          </div>
        </el-form-item>

        <!-- 引用选择配置（仅引用选择类型显示） -->
        <el-form-item
          v-if="showQuoteConfig"
          label="引用配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.quoteConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleQuoteTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.quoteConfig!.displayField"
                placeholder="请选择显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.quoteConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in quoteDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>

        <!-- 状态徽标配置（仅状态徽标类型显示） -->
        <el-form-item
          v-if="showStatusBadgeConfig"
          label="状态选项"
        >
          <div class="status-badge-config">
            <div
              v-for="(opt, optIndex) in fieldFormData.statusBadgeConfig.options"
              :key="optIndex"
              class="status-badge-option-row"
            >
              <el-input v-model="opt.label" placeholder="显示文本" style="width: 18%" />
              <el-input v-model="opt.value" placeholder="值" style="width: 16%" />
              <IconPicker v-model="opt.icon" style="width: 22%" />
              <el-color-picker v-model="opt.color" />
              <el-checkbox v-model="opt.animated">动画</el-checkbox>
              <el-checkbox v-model="opt.terminal">终态</el-checkbox>
              <el-button type="danger" link @click="removeStatusBadgeOption(optIndex)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button type="primary" link @click="addStatusBadgeOption">
              <el-icon><Plus /></el-icon>
              添加阶段
            </el-button>

            <div class="status-badge-timing">
              <el-form-item label="轮询间隔(秒)" label-width="110px">
                <el-input-number
                  v-model="fieldFormData.statusBadgeConfig.pollIntervalSec"
                  :min="1"
                  :max="3600"
                />
              </el-form-item>
              <el-form-item label="超时(秒)" label-width="90px">
                <el-input-number
                  v-model="fieldFormData.statusBadgeConfig.timeoutSec"
                  :min="1"
                  placeholder="不填=不启用"
                />
              </el-form-item>
              <el-form-item label="超时后写为" label-width="100px">
                <el-select
                  v-model="fieldFormData.statusBadgeConfig.timeoutValue"
                  placeholder="选一个选项值"
                  clearable
                  style="width: 160px"
                >
                  <el-option
                    v-for="opt in fieldFormData.statusBadgeConfig.options"
                    :key="opt.value"
                    :label="opt.label || opt.value"
                    :value="opt.value"
                  />
                </el-select>
              </el-form-item>
            </div>
          </div>
        </el-form-item>

        <!-- 文件类型约束（仅文件/图片上传类型显示） -->
        <el-form-item
          v-if="showFileConfig"
          label="允许的文件类型"
        >
          <div class="file-config">
            <el-select
              v-model="fieldFormData.fileConfig.allowedExtensions"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="留空表示不限制，如 .pdf、.docx"
              style="width: 100%"
            />
            <div class="file-config-tip">
              输入扩展名后回车添加，如 pdf、.docx；留空表示不限制上传的文件类型
            </div>
          </div>
        </el-form-item>

        <!-- 工作流配置（仅 select 类型显示） -->
        <el-form-item
          v-if="showWorkflowConfig"
          label="工作流"
        >
          <div class="workflow-config">
            <el-switch
              v-model="workflowEnabled"
              active-text="启用工作流"
              inactive-text=""
            />

            <template v-if="workflowEnabled">
              <div class="workflow-transitions">
                <div
                  v-for="(t, tIdx) in workflowTransitions"
                  :key="tIdx"
                  class="transition-row"
                >
                  <el-select v-model="t.from" placeholder="源状态" style="width: 100px" size="small">
                    <el-option label="任意(*)" value="*" />
                    <el-option
                      v-for="opt in fieldFormData.options"
                      :key="String(opt.value)"
                      :label="opt.label"
                      :value="String(opt.value)"
                    />
                  </el-select>
                  <span style="margin: 0 4px; color: #909399">&rarr;</span>
                  <el-select v-model="t.to" placeholder="目标状态" style="width: 100px" size="small">
                    <el-option
                      v-for="opt in fieldFormData.options"
                      :key="String(opt.value)"
                      :label="opt.label"
                      :value="String(opt.value)"
                    />
                  </el-select>
                  <el-input v-model="t.label" placeholder="按钮名" style="width: 80px" size="small" />
                  <el-select v-model="t.roles" multiple placeholder="角色(空=全部)" style="width: 140px" size="small">
                    <el-option label="管理员" value="admin" />
                    <el-option label="开发者" value="developer" />
                    <el-option label="访客" value="guest" />
                  </el-select>
                  <el-button type="danger" link size="small" @click="workflowTransitions.splice(tIdx, 1)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
                <el-button type="primary" link size="small" @click="addWorkflowTransition">
                  <el-icon><Plus /></el-icon>
                  添加转换规则
                </el-button>
              </div>
            </template>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSaveField">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * FieldConfigEditor 组件脚本
 *
 * Props：
 * - pageId: 页面ID
 * - fields: 字段配置列表
 *
 * Events：
 * - update: 字段配置更新
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Check, Rank, Delete, DocumentAdd, Upload } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import type { FieldConfig, FieldFormData } from '@/types'
import { CONTROL_TYPE_OPTIONS, createEmptyFieldFormData } from '@/types'
import { usePageConfigStore } from '@/stores'
import { v4 as uuidv4 } from 'uuid'
import { IconPicker } from '@/components/common'
import { getFieldIndexStatuses, type FieldIndexStatus } from '@/api/page'

// ==================== Props & Emits ====================

interface Props {
  /** 页面ID */
  pageId: string
  /** 字段配置列表 */
  fields: FieldConfig[]
  /**
   * 页面已有数据时,锁定已存在字段的编辑/删除入口
   * (重命名/改类型会让旧 JSONB 数据语义错位)。新字段始终可加。
   */
  lockExistingFields?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  lockExistingFields: false,
})
const emit = defineEmits<{
  (e: 'update', fields: FieldConfig[]): void
  (e: 'import'): void
}>()

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()

// ==================== Refs ====================

const fieldFormRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 本地字段列表（用于编辑）
 */
const localFields = ref<FieldConfig[]>([])

/**
 * 字段编辑表单数据
 */
const fieldFormData = ref<FieldFormData>(createEmptyFieldFormData())

/**
 * 编辑对话框可见性
 */
const editDialogVisible = ref(false)

/**
 * 当前编辑的字段索引（-1表示新增）
 */
const editingIndex = ref(-1)

/**
 * 保存加载状态
 */
const saving = ref(false)

// ==================== 常量 ====================

/**
 * 控件类型选项
 */
const controlTypeOptions = CONTROL_TYPE_OPTIONS

/**
 * 字段表单验证规则
 */
const fieldFormRules: FormRules = {
  label: [
    { required: true, message: '请输入显示名称', trigger: 'blur' }
  ],
  fieldName: [
    { required: true, message: '请输入字段名', trigger: 'blur' },
    { pattern: /^[a-zA-Z][a-zA-Z0-9_]*$/, message: '字段名必须以字母开头，只能包含字母、数字和下划线', trigger: 'blur' }
  ],
  controlType: [
    { required: true, message: '请选择控件类型', trigger: 'change' }
  ]
}

// ==================== 计算属性 ====================

/**
 * 编辑对话框标题
 */
const editDialogTitle = computed(() => {
  return editingIndex.value === -1 ? '添加字段' : '编辑字段'
})

/**
 * 是否显示选项配置
 */
const showOptionsConfig = computed(() => {
  const optionTypes = ['select', 'multiSelect', 'radio', 'checkbox']
  return optionTypes.includes(fieldFormData.value.controlType)
})

// 可设默认值的控件类型（输入类 + 选择类 + 日期）
const showDefaultValue = computed(() => {
  return ['text', 'textarea', 'number', 'select', 'multiSelect', 'radio', 'checkbox', 'date', 'datetime', 'statusBadge']
    .includes(fieldFormData.value.controlType)
})

const showRelationConfig = computed(() => {
  return fieldFormData.value.controlType === 'relation'
})

const showReferenceConfig = computed(() => {
  return fieldFormData.value.controlType === 'reference'
})

const showSequenceConfig = computed(() => {
  return fieldFormData.value.controlType === 'autoSequence'
})

const showQuoteConfig = computed(() => {
  return fieldFormData.value.controlType === 'quoteSelect'
})

const showCompositeTextConfig = computed(() => {
  return fieldFormData.value.controlType === 'compositeText'
})

const showStatusBadgeConfig = computed(() => {
  return fieldFormData.value.controlType === 'statusBadge'
})

const showFileConfig = computed(() => {
  return ['file', 'image'].includes(fieldFormData.value.controlType)
})

/**
 * 归一化用户输入的扩展名列表：补前导点、转小写、去空白、去重。
 * 用户可能输入 "pdf"、".PDF"、" .pdf " 等形式，统一存成 ".pdf"。
 */
function normalizeExtensions(raw: string[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of raw) {
    const trimmed = item.trim().toLowerCase()
    if (!trimmed) continue
    const ext = trimmed.startsWith('.') ? trimmed : `.${trimmed}`
    if (!seen.has(ext)) {
      seen.add(ext)
      result.push(ext)
    }
  }
  return result
}

const compositeSourceFieldOptions = computed(() => {
  return localFields.value
    .filter(f => ['text', 'textarea'].includes(f.controlType)
      && f.fieldName !== fieldFormData.value.fieldName)
    .map(f => ({ label: `${f.label}（${f.fieldName}）`, value: f.fieldName }))
})

const compositeTextPreview = computed(() => {
  const cfg = fieldFormData.value.compositeTextConfig
  if (!cfg || !cfg.sourceFields?.length) return ''
  const labels = cfg.sourceFields.map(fn => {
    const f = localFields.value.find(f => f.fieldName === fn)
    return f?.label || fn
  })
  return labels.join(cfg.separator || ' - ')
})

const showWorkflowConfig = computed(() => {
  return fieldFormData.value.controlType === 'select'
})

// 与 server/utils/field_indexes.py::INDEXABLE_TYPES 保持一致：值是标量、
// 适合做等值/范围/排序表达式索引的控件类型。
const INDEXABLE_TYPES = [
  'text', 'number', 'select', 'radio', 'date', 'datetime',
  'autoSequence', 'autoTimestamp', 'compositeText', 'statusBadge', 'checkbox',
]

const showIndexedToggle = computed(() => {
  return INDEXABLE_TYPES.includes(fieldFormData.value.controlType)
})

const workflowEnabled = ref(false)
const workflowTransitions = ref<Array<{ from: string; to: string; label: string; roles: string[] }>>([])

function addWorkflowTransition() {
  workflowTransitions.value.push({ from: '*', to: '', label: '', roles: [] })
}

const sequencePreview = computed(() => {
  const cfg = fieldFormData.value.sequenceConfig
  if (!cfg) return ''
  const padLen = String(cfg.max).length
  const first = `${cfg.prefix}${String(1).padStart(padLen, '0')}`
  const last = `${cfg.prefix}${String(cfg.max).padStart(padLen, '0')}`
  return `${first} ~ ${last}`
})

/**
 * 目标集合下拉选项（排除当前页面）
 */
const collectionOptions = computed(() => {
  return pageConfigStore.pageConfigs
    .filter((c) => c.id !== props.pageId)
    .map((c) => ({
      label: c.name,
      value: c.id.replace('page-', '')
    }))
})

/**
 * 选中目标集合的字段列表
 */
const targetCollectionFields = computed(() => {
  const tc = fieldFormData.value.relationConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 显示字段下拉选项（目标集合的非关联字段）
 */
const displayFieldOptions = computed(() => {
  return targetCollectionFields.value
    .filter((f) => f.controlType !== 'relation')
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 反向字段下拉选项（目标集合的关联字段）
 */
const targetFieldOptions = computed(() => {
  return targetCollectionFields.value
    .filter((f) => f.controlType === 'relation')
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 引用配置：选中目标集合的字段列表
 */
const refTargetCollectionFields = computed(() => {
  const tc = fieldFormData.value.referenceConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 引用配置：显示字段和继承字段的选项（目标集合的非关联/非引用字段）
 */
const refDisplayFieldOptions = computed(() => {
  return refTargetCollectionFields.value
    .filter((f) => !['relation', 'reference'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 引用选择配置：选中目标集合的字段列表
 */
const quoteTargetCollectionFields = computed(() => {
  const tc = fieldFormData.value.quoteConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 引用选择配置：显示字段的选项（目标集合的非关联/非引用字段）
 */
const quoteDisplayFieldOptions = computed(() => {
  return quoteTargetCollectionFields.value
    .filter((f) => !['relation', 'reference', 'quoteSelect'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 选项配置 - 数据页列表（可选择包含自身页面，因为选项数据和当前页面之间没有约束）
 */
const optionsCollectionList = computed(() => {
  return pageConfigStore.pageConfigs.map((c) => ({
    label: c.name,
    value: c.id.replace('page-', '')
  }))
})

/**
 * 选项配置 - 选中数据页的字段列表（非关联/非引用类字段）
 */
const optionsCollectionFieldList = computed(() => {
  const tc = fieldFormData.value.optionsSource?.collection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
    .filter((f) => !['relation', 'reference', 'quoteSelect', 'file', 'image'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

// ==================== 方法 ====================

/**
 * 获取控件类型标签
 */
function getControlTypeLabel(type: string): string {
  const option = controlTypeOptions.find((opt) => opt.value === type)
  return option?.label || type
}

/**
 * 处理目标集合变更，清空依赖字段
 */
function handleTargetCollectionChange(): void {
  if (fieldFormData.value.relationConfig) {
    fieldFormData.value.relationConfig.displayField = ''
    fieldFormData.value.relationConfig.targetField = ''
  }
}

/**
 * 处理引用目标集合变更，清空依赖字段
 */
function handleRefTargetCollectionChange(): void {
  if (fieldFormData.value.referenceConfig) {
    fieldFormData.value.referenceConfig.displayField = ''
    fieldFormData.value.referenceConfig.inheritFields = []
  }
}

/**
 * 处理引用选择目标集合变更，清空依赖字段
 */
function handleQuoteTargetCollectionChange(): void {
  if (fieldFormData.value.quoteConfig) {
    fieldFormData.value.quoteConfig.displayField = ''
  }
}

/**
 * 处理选项数据页变更，清空标签字段和值字段
 */
function handleOptionsCollectionChange(): void {
  fieldFormData.value.optionsSource.labelField = ''
  fieldFormData.value.optionsSource.valueField = ''
}

/**
 * 处理添加字段
 */
function handleAddField(): void {
  editingIndex.value = -1
  fieldFormData.value = createEmptyFieldFormData(localFields.value.length + 1)
  workflowEnabled.value = false
  workflowTransitions.value = []
  editDialogVisible.value = true
}

/**
 * 处理编辑字段
 */
function handleEditField(field: FieldConfig, index: number): void {
  editingIndex.value = index
  fieldFormData.value = {
    id: field.id,
    label: field.label,
    fieldName: field.fieldName,
    controlType: field.controlType,
    required: field.required,
    order: field.order,
    placeholder: field.placeholder || '',
    defaultValue: field.defaultValue,
    options: field.options ? [...field.options] : [],
    optionsSource: field.optionsSource
      ? { ...field.optionsSource }
      : { type: 'static' },
    relationConfig: field.relationConfig
      ? { ...field.relationConfig }
      : { targetCollection: '', displayField: '', targetField: '' },
    isPrimaryKey: field.isPrimaryKey || false,
    referenceConfig: field.referenceConfig
      ? { ...field.referenceConfig, inheritFields: [...(field.referenceConfig.inheritFields || [])] }
      : { targetCollection: '', displayField: '', inheritFields: [] },
    sequenceConfig: field.sequenceConfig
      ? { ...field.sequenceConfig }
      : { prefix: '', max: 999 },
    quoteConfig: field.quoteConfig
      ? { ...field.quoteConfig }
      : { targetCollection: '', displayField: '' },
    compositeTextConfig: field.compositeTextConfig
      ? { ...field.compositeTextConfig, sourceFields: [...field.compositeTextConfig.sourceFields] }
      : { sourceFields: [], separator: ' - ' },
    statusBadgeConfig: field.statusBadgeConfig
      ? { ...field.statusBadgeConfig, options: field.statusBadgeConfig.options.map(o => ({ ...o })) }
      : { options: [], pollIntervalSec: 5 },
    indexed: field.indexed || false,
    fileConfig: field.fileConfig
      ? { allowedExtensions: [...field.fileConfig.allowedExtensions] }
      : { allowedExtensions: [] }
  }
  // Load workflow config
  const wf = field.workflowConfig
  workflowEnabled.value = wf?.enabled || false
  workflowTransitions.value = wf?.transitions
    ? wf.transitions.map(t => ({ from: t.from, to: t.to, label: t.label, roles: t.roles || [] }))
    : []
  editDialogVisible.value = true
}

/**
 * 处理删除字段
 */
function handleDeleteField(index: number): void {
  localFields.value.splice(index, 1)
  // 重新排序
  localFields.value.forEach((field, i) => {
    field.order = i + 1
  })
}

/**
 * 处理保存字段
 */
async function handleSaveField(): Promise<void> {
  const valid = await fieldFormRef.value?.validate()
  if (!valid) return

  const fieldData: FieldConfig = {
    id: fieldFormData.value.id || `field-${uuidv4().slice(0, 8)}`,
    label: fieldFormData.value.label,
    fieldName: fieldFormData.value.fieldName,
    controlType: fieldFormData.value.controlType,
    required: fieldFormData.value.required,
    order: fieldFormData.value.order,
    placeholder: fieldFormData.value.placeholder,
    defaultValue: fieldFormData.value.defaultValue,
    options: showOptionsConfig.value
      ? fieldFormData.value.options.map(o => ({
          label: o.label,
          value: o.value || o.label   // auto-fill empty value with label
        }))
      : undefined,
    optionsSource: showOptionsConfig.value ? fieldFormData.value.optionsSource : undefined,
    relationConfig: showRelationConfig.value ? fieldFormData.value.relationConfig : undefined,
    isPrimaryKey: fieldFormData.value.isPrimaryKey || undefined,
    referenceConfig: showReferenceConfig.value ? fieldFormData.value.referenceConfig : undefined,
    sequenceConfig: showSequenceConfig.value ? fieldFormData.value.sequenceConfig : undefined,
    quoteConfig: showQuoteConfig.value ? fieldFormData.value.quoteConfig : undefined,
    compositeTextConfig: showCompositeTextConfig.value ? fieldFormData.value.compositeTextConfig : undefined,
    statusBadgeConfig: showStatusBadgeConfig.value
      ? {
          ...fieldFormData.value.statusBadgeConfig,
          options: fieldFormData.value.statusBadgeConfig.options.filter(o => o.value && o.label),
        }
      : undefined,
    workflowConfig: showWorkflowConfig.value && workflowEnabled.value
      ? {
          enabled: true,
          transitions: workflowTransitions.value
            .filter(t => t.from && t.to)
            .map(t => ({
              from: t.from,
              to: t.to,
              label: t.label || `${t.from}→${t.to}`,
              roles: t.roles.length > 0 ? t.roles : undefined,
            })),
        }
      : undefined,
    indexed: showIndexedToggle.value ? (fieldFormData.value.indexed || undefined) : undefined,
    fileConfig: showFileConfig.value
      ? { allowedExtensions: normalizeExtensions(fieldFormData.value.fileConfig.allowedExtensions) }
      : undefined
  }

  if (editingIndex.value === -1) {
    // 新增
    localFields.value.push(fieldData)
  } else {
    // 编辑
    localFields.value[editingIndex.value] = fieldData
  }

  editDialogVisible.value = false
  ElMessage.success('字段配置已更新，请点击「保存配置」按钮保存到服务器')
}

/**
 * 处理拖拽结束
 */
function handleDragEnd(): void {
  // 重新排序
  localFields.value.forEach((field, i) => {
    field.order = i + 1
  })
}

/**
 * 处理保存所有配置
 */
async function handleSaveAll(): Promise<void> {
  saving.value = true
  try {
    emit('update', [...localFields.value])
  } finally {
    saving.value = false
  }
}

/**
 * 添加选项
 */
function addOption(): void {
  fieldFormData.value.options.push({ label: '', value: '' })
}

/**
 * 移除选项
 */
function removeOption(index: number): void {
  fieldFormData.value.options.splice(index, 1)
}

/**
 * 添加状态徽标阶段选项
 */
function addStatusBadgeOption(): void {
  fieldFormData.value.statusBadgeConfig.options.push({ value: '', label: '', icon: '' })
}

/**
 * 移除状态徽标阶段选项
 */
function removeStatusBadgeOption(index: number): void {
  fieldFormData.value.statusBadgeConfig.options.splice(index, 1)
}

/**
 * 批量添加对话框状态
 */
const batchDialogVisible = ref(false)
const batchOptionsText = ref('')

/**
 * 确认批量添加选项
 *
 * 每行解析为一个选项，支持 "标签:值" 格式，省略值时与标签相同。
 */
function confirmBatchAdd(): void {
  const lines = batchOptionsText.value.split('\n').map((l) => l.trim()).filter(Boolean)
  if (lines.length === 0) {
    ElMessage.warning('请输入至少一个选项')
    return
  }
  for (const line of lines) {
    const sepIdx = line.indexOf(':')
    if (sepIdx > 0) {
      const label = line.slice(0, sepIdx).trim()
      const value = line.slice(sepIdx + 1).trim() || label
      fieldFormData.value.options.push({ label, value })
    } else {
      fieldFormData.value.options.push({ label: line, value: line })
    }
  }
  batchOptionsText.value = ''
  batchDialogVisible.value = false
  ElMessage.success(`已添加 ${lines.length} 个选项`)
}

// ==================== 监听 ====================

/**
 * Snapshot of field names that existed when the editor opened — these are the
 * ones we have to lock when the underlying collection already has data.
 * Fields added during this session are tracked as "new" and stay editable.
 *
 * Must be declared BEFORE the `watch` below — that watch runs with
 * `immediate: true` and writes to this ref on first tick.
 */
const originalFieldNames = ref<Set<string>>(new Set())

function isLockedField(fieldName: string): boolean {
  return props.lockExistingFields && originalFieldNames.value.has(fieldName)
}

// ==================== 字段索引构建状态 ====================
// 建索引在后台异步跑（CREATE INDEX CONCURRENTLY 在大表上可能耗时很久），
// 这里轮询展示进度：待建/构建中/已就绪/失败（见 utils/field_index_scheduler.py）。
// 声明必须在下方 watch(props.fields, { immediate: true }) 之前——该 watch
// 立即同步调用 fetchFieldIndexStatuses()，晚声明会撞上 let 的暂时性死区。

const fieldIndexStatuses = ref<Record<string, FieldIndexStatus>>({})
let fieldIndexPollTimer: ReturnType<typeof setTimeout> | null = null

const editingFieldIndexStatus = computed(() => {
  const fieldName = fieldFormData.value.fieldName
  return fieldName ? fieldIndexStatuses.value[fieldName] : undefined
})

function fieldIndexStatusLabel(status: FieldIndexStatus['status']): string {
  return {
    pending: '待建',
    building: '构建中',
    ready: '已就绪',
    failed: '失败',
    dropping: '清理中',
  }[status] || status
}

function fieldIndexStatusTagType(status: FieldIndexStatus['status']): 'info' | 'warning' | 'success' | 'danger' {
  if (status === 'ready') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'pending' || status === 'building') return 'warning'
  return 'info'
}

async function fetchFieldIndexStatuses(): Promise<void> {
  if (fieldIndexPollTimer) {
    clearTimeout(fieldIndexPollTimer)
    fieldIndexPollTimer = null
  }
  if (!props.pageId) return
  try {
    const resp = await getFieldIndexStatuses(props.pageId)
    const map: Record<string, FieldIndexStatus> = {}
    for (const s of resp.data) map[s.fieldName] = s
    fieldIndexStatuses.value = map
    const hasInFlight = resp.data.some(s => s.status === 'pending' || s.status === 'building' || s.status === 'dropping')
    if (hasInFlight) {
      fieldIndexPollTimer = setTimeout(fetchFieldIndexStatuses, 5000)
    }
  } catch {
    // 状态展示是锦上添花，拉取失败不影响字段配置本身的编辑/保存
  }
}

onUnmounted(() => {
  if (fieldIndexPollTimer) clearTimeout(fieldIndexPollTimer)
})

/**
 * 监听 props.fields 变化，同步到本地
 */
watch(
  () => props.fields,
  (newFields) => {
    localFields.value = newFields.map((f) => ({ ...f }))
    // Refresh the "originals" snapshot so locked-when-has-data semantics
    // pin to the fields that existed at edit-time, not to whatever's been
    // added during this session.
    originalFieldNames.value = new Set(newFields.map((f) => f.fieldName))
    fetchFieldIndexStatuses()
  },
  { immediate: true, deep: true }
)
</script>

<style scoped lang="scss">
.field-config-editor {
  width: 100%;
}

.default-value-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}

.editor-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.field-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
  border: 1px solid #e4e7ed;

  &:hover {
    border-color: #c0c4cc;
  }

  .drag-handle {
    cursor: move;
    padding: 0 8px;
    color: #909399;

    &:hover {
      color: #409eff;
    }
  }

  .field-content {
    flex: 1;
    margin: 0 12px;

    .field-main {
      display: flex;
      align-items: center;
      gap: 8px;

      .field-label {
        font-weight: 500;
        color: #303133;
      }
    }

    .field-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;
    }
  }

  .field-actions {
    display: flex;
    gap: 4px;
  }
}

.options-config {
  width: 100%;

  .options-type {
    margin-bottom: 12px;
  }

  .static-options {
    .option-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }

    .option-actions {
      display: flex;
      gap: 16px;
    }

    .batch-hint {
      margin: 0 0 12px;
      font-size: 13px;
      color: #909399;

      code {
        background: #f5f7fa;
        padding: 1px 4px;
        border-radius: 3px;
        font-size: 12px;
      }
    }
  }

  .api-options {
    padding-top: 8px;
  }

  .collection-options {
    padding-top: 8px;
  }
}

.sequence-preview {
  margin-top: 8px;
  padding: 8px 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
}

.file-config {
  width: 100%;

  .file-config-tip {
    margin-top: 6px;
    font-size: 12px;
    color: #909399;
  }
}

.workflow-config {
  width: 100%;
}

.workflow-transitions {
  margin-top: 8px;
}

.transition-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.status-badge-config {
  width: 100%;
}

.status-badge-option-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.status-badge-timing {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 8px;

  :deep(.el-form-item) {
    margin-bottom: 0;
  }
}

.indexed-toggle-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.indexed-hint {
  font-size: 12px;
  color: #909399;
}

.indexed-error {
  margin-top: 4px;
  font-size: 12px;
  color: #f56c6c;
}
</style>
