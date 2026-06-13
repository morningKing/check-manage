/**
 * 动态数据页面
 *
 * 职责：
 * - 根据页面配置动态渲染数据页面
 * - 集成数据表格和表单
 * - 实现数据的增删改查功能
 *
 * 核心功能：
 * - 从路由参数获取 pageId
 * - 从 Store 获取页面配置和字段配置
 * - 动态渲染数据表格
 * - 提供新增/编辑对话框
 */
<template>
  <div class="dynamic-page" v-loading="pageLoading">
    <!-- 页面标题和操作栏 -->
    <div class="page-header">
      <div class="page-title">
        <!-- 标题行：标题 + 副标题 + 分支标签 + 切换按钮 -->
        <div class="title-row">
          <h2>{{ pageConfig?.name || '数据页面' }}</h2>
          <span v-if="pageConfig?.description" class="page-subtitle">{{ pageConfig.description }}</span>
          <!-- 分支标签（紧挨着标题后面） -->
          <el-tag
            :type="currentBranch?.branchId ? 'primary' : 'success'"
            size="small"
          >
            {{ currentBranch?.branchName || '主分支' }}
          </el-tag>
          <!-- 切换下拉按钮（非访客且属于项目时可见；后端切换分支为 write 权限，与此一致） -->
          <el-dropdown
            v-if="!isGuest && projectMenuId"
            trigger="click"
            @command="handleBranchSwitch"
            @visible-change="(visible: boolean) => visible && loadBranchVersions()"
          >
            <span class="branch-switch-link">
              切换 <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu class="branch-dropdown-menu">
                <el-dropdown-item
                  :command="'main'"
                  :disabled="!currentBranch?.branchId"
                >
                  <el-icon v-if="!currentBranch?.branchId"><Check /></el-icon>
                  主分支
                </el-dropdown-item>
                <el-dropdown-item
                  v-for="branch in branchVersions"
                  :key="branch.id"
                  :command="branch.id"
                  :disabled="branch.id === currentBranch?.branchId"
                >
                  <el-icon v-if="branch.id === currentBranch?.branchId"><Check /></el-icon>
                  {{ branch.name }}
                </el-dropdown-item>
                <el-dropdown-item v-if="isAdmin" divided command="manage">
                  <el-icon><Tickets /></el-icon>
                  管理版本...
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
      <div class="page-actions">
        <!-- 检索区：三合一统一检索 -->
        <div v-if="viewMode !== 'excel'" class="search-zone">
          <el-input
            v-if="searchMode === 'keyword'"
            v-model="searchKeyword"
            placeholder="搜索..."
            clearable
            :prefix-icon="Search"
            class="header-search"
          />
          <el-input
            v-else-if="searchMode === 'ai'"
            v-model="aiSearchText"
            placeholder="用自然语言描述查询条件，回车执行"
            clearable
            class="header-search header-search--wide"
            @keydown.enter="executeAiQuery"
          >
            <template #prefix><el-icon><MagicStick /></el-icon></template>
            <template #suffix>
              <el-icon v-if="aiSearchLoading" class="is-loading"><Loading /></el-icon>
            </template>
          </el-input>
          <el-input
            v-else
            v-model="mongoQueryText"
            placeholder='{"field": {"$regex": "value"}}'
            class="header-search header-search--wide header-search--mono"
            @keydown.ctrl.enter="executeMongoQuery"
          >
            <template #prefix><el-icon><DCaret /></el-icon></template>
            <template #suffix>
              <el-popover placement="bottom-end" :width="420" trigger="click">
                <template #reference>
                  <el-icon class="query-help-icon"><QuestionFilled /></el-icon>
                </template>
                <div class="query-help">
                  <p><b>MongoDB 查询语法</b>（支持中文字段名）</p>
                  <table>
                    <tr><td><code>{"用例ID": "IC-001"}</code></td><td>精确匹配</td></tr>
                    <tr><td><code>{"名称": {"$regex": "test"}}</code></td><td>正则匹配（不区分大小写）</td></tr>
                    <tr><td><code>{"名称": {"$like": "test"}}</code></td><td>模糊匹配（%test%）</td></tr>
                    <tr><td><code>{"age": {"$gt": 18}}</code></td><td>大于（$gte, $lt, $lte 类似）</td></tr>
                    <tr><td><code>{"状态": {"$in": ["a","b"]}}</code></td><td>在列表中</td></tr>
                    <tr><td><code>{"状态": {"$nin": ["a"]}}</code></td><td>不在列表中</td></tr>
                    <tr><td><code>{"field": {"$ne": "val"}}</code></td><td>不等于</td></tr>
                    <tr><td><code>{"field": {"$exists": true}}</code></td><td>字段存在/不存在</td></tr>
                    <tr><td><code>{"$or": [{...}, {...}]}</code></td><td>逻辑或</td></tr>
                    <tr><td><code>{"$and": [{...}, {...}]}</code></td><td>逻辑与</td></tr>
                  </table>
                  <p style="margin-top:8px;color:#909399;font-size:12px">Ctrl+Enter 快捷执行 · 字段名可用中文标签或英文字段名</p>
                </div>
              </el-popover>
            </template>
          </el-input>

          <!-- 模式选择器 -->
          <el-select
            :model-value="searchMode"
            class="search-mode-select"
            @update:model-value="setSearchMode"
          >
            <el-option label="关键字" value="keyword" />
            <el-option label="AI 智能" value="ai" />
            <el-option label="高级查询" value="mongo" />
          </el-select>

          <!-- 生效中的查询 chip -->
          <el-tooltip v-if="aiGeneratedFilter" placement="bottom">
            <template #content>
              <pre style="margin:0;max-width:400px;white-space:pre-wrap">{{ JSON.stringify(aiGeneratedFilter, null, 2) }}</pre>
            </template>
            <el-tag type="warning" closable class="search-chip" @close="clearAiQuery">
              <el-icon style="vertical-align: -2px"><MagicStick /></el-icon> AI 筛选
            </el-tag>
          </el-tooltip>
          <el-tag
            v-else-if="activeMongoQuery"
            type="primary"
            closable
            class="search-chip"
            @close="clearMongoQuery"
          >
            ⟨⟩ 高级
          </el-tag>
          <span class="actions-divider"></span>
        </div>
        <ViewSelector
          @select="handleViewSelect"
          @manage="handleOpenManage"
        />
        <el-radio-group v-model="viewMode" size="small" class="view-toggle">
          <el-radio-button value="table"><el-icon><Grid /></el-icon></el-radio-button>
          <el-radio-button value="excel"><el-icon><Document /></el-icon></el-radio-button>
          <el-radio-button v-if="hasKanbanConfig" value="kanban"><el-icon><Operation /></el-icon></el-radio-button>
          <el-radio-button v-if="hasCalendarConfig" value="calendar"><el-icon><Calendar /></el-icon></el-radio-button>
          <el-radio-button v-if="hasGanttConfig" value="gantt"><el-icon><DataLine /></el-icon></el-radio-button>
        </el-radio-group>
        <el-button v-if="!isGuest && canCreate" type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新增
        </el-button>
        <el-dropdown @command="handleMoreCommand" trigger="click">
          <el-button>
            操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="refresh" :icon="Refresh">刷新</el-dropdown-item>

              <el-dropdown-item divided disabled class="dropdown-group-label">导入 / 导出</el-dropdown-item>
              <el-dropdown-item command="export" :icon="Download">导出 Excel</el-dropdown-item>
              <el-dropdown-item
                v-for="s in boundExportScripts"
                :key="s.id"
                :command="'script:' + s.id"
                :icon="Download"
              >
                {{ s.name }} ({{ s.outputFormat }})
              </el-dropdown-item>
              <el-dropdown-item v-if="!isGuest" command="import" :icon="Upload">导入数据</el-dropdown-item>
              <el-dropdown-item v-if="!isGuest" command="template" :icon="Download">下载导入模板</el-dropdown-item>

              <template v-if="!isGuest && hasReferenceFields">
                <el-dropdown-item divided disabled class="dropdown-group-label">引用 / 关系</el-dropdown-item>
                <el-dropdown-item command="reResolveRefs" :icon="RefreshRight">重新解析引用</el-dropdown-item>
              </template>

              <template v-if="isAdmin">
                <el-dropdown-item divided disabled class="dropdown-group-label">数据治理</el-dropdown-item>
                <el-dropdown-item command="version" :icon="Tickets">版本管理</el-dropdown-item>
                <el-dropdown-item command="dependency" :icon="Operation">依赖管理</el-dropdown-item>
                <el-dropdown-item command="copyCollection" :icon="CopyDocument">复制 collection 名</el-dropdown-item>
              </template>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <!-- 跳转来源提示栏 -->
    <div v-if="jumpSource" class="jump-source-bar">
      <el-icon class="jump-source-icon"><Back /></el-icon>
      <span>从「<strong>{{ jumpSource.pageName }}</strong>」跳转而来</span>
      <el-button type="primary" link size="small" @click="handleJumpBack">
        <el-icon><Back /></el-icon>
        返回
      </el-button>
      <el-button type="info" link size="small" @click="dismissJumpBar">
        关闭
      </el-button>
    </div>

    <!-- 数据表格 -->
    <el-card v-show="viewMode === 'table'" class="table-card">
      <DataTable
        ref="dataTableRef"
        :data="paginatedData"
        :fields="effectiveFields"
        :loading="tableLoading"
        :total="totalCount"
        :show-pagination="true"
        :show-actions="!isGuest"
        :can-update="canUpdate"
        :can-delete="canDelete"
        show-selection
        @view="handleView"
        @edit="handleEdit"
        @delete="handleDeleteConfirm"
        @reference-click="handleReferenceClick"
        @relation-click="handleRelationClick"
        @quote-click="handleQuoteClick"
        @selection-change="handleSelectionChange"
        @page-change="handlePageChange"
        @filter-change="handleFilterChange"
      >
        <template #extra-actions="{ row }">
          <el-button
            v-if="!isGuest && canCreate"
            type="success"
            link
            @click="handleCopy(row)"
          >
            复制
          </el-button>
          <el-button type="info" link @click="handleShowRelationGraph(row)">
            图谱
          </el-button>
          <el-dropdown
            v-if="boundRowExportScripts.length > 1"
            @command="(cmd: string) => handleRowExport(cmd, row)"
            trigger="click"
          >
            <el-button type="warning" link>
              导出<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="s in boundRowExportScripts"
                  :key="s.id"
                  :command="s.id"
                >
                  {{ s.name }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            v-else-if="boundRowExportScripts.length === 1"
            type="warning"
            link
            @click="handleRowExport(boundRowExportScripts[0].id, row)"
          >
            导出
          </el-button>
        </template>
      </DataTable>
    </el-card>

    <!-- 看板视图 -->
    <el-card v-show="viewMode === 'kanban'" class="table-card kanban-card">
      <KanbanBoard
        v-if="kanbanConfig"
        :data="filteredData"
        :group-field="kanbanConfig.groupField"
        :group-options="kanbanGroupOptions"
        :card-title="kanbanConfig.cardTitle"
        :card-fields="kanbanConfig.cardFields || []"
        :fields="pageFields"
        :column-order="kanbanConfig.columnOrder"
        :card-color-field="kanbanConfig.cardColorField"
        :search-keyword="searchKeyword"
        @card-move="handleKanbanCardMove"
        @card-click="handleView"
      />
    </el-card>

    <!-- 日历视图 -->
    <el-card v-show="viewMode === 'calendar'" class="table-card calendar-card">
      <CalendarView
        ref="calendarViewRef"
        v-if="calendarConfig"
        :data="filteredData"
        :fields="pageFields"
        :config="calendarConfig"
        @card-click="handleView"
        @date-change="handleCalendarDateChange"
        @date-click="handleCalendarDateClick"
      />
    </el-card>

    <!-- 甘特图视图 -->
    <el-card v-show="viewMode === 'gantt'" class="table-card gantt-card">
      <GanttView
        v-if="ganttConfig"
        :data="filteredData"
        :fields="pageFields"
        :config="ganttConfig"
        @task-click="handleView"
      />
    </el-card>

    <!-- Excel 视图 - 使用全量数据 -->
    <el-card v-show="viewMode === 'excel'" class="table-card excel-card">
      <!-- loading placeholder 覆盖在 ExcelView 上方 -->
      <div v-show="!excelReady || excelLoading" class="excel-loading-placeholder">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在加载 Excel 视图...</span>
      </div>
      <!-- ExcelView 延迟挂载，挂载后用 v-show 控制可见性 -->
      <ExcelView
        v-if="excelInitialized"
        v-show="excelReady && !excelLoading"
        ref="excelViewRef"
        :data="excelData"
        :fields="effectiveFields"
        :loading="excelLoading"
        :collection-id="pageId"
        @row-click="handleView"
        @reference-click="handleReferenceClick"
        @relation-click="handleRelationClick"
        @quote-click="handleQuoteClick"
      />
    </el-card>

    <!-- 新增/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      width="600px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <template #header>
        <div style="display: flex; align-items: center;">
          <span style="font-weight: bold;">{{ dialogTitle }}</span>
          <el-tag
            v-if="columnViewStore.currentView"
            type="info"
            size="small"
            style="margin-left: 8px;"
          >
            {{ columnViewStore.currentView.name }}
          </el-tag>
          <el-tag
            :type="currentBranch?.branchId ? 'primary' : 'success'"
            size="small"
            style="margin-left: auto;"
          >
            {{ currentBranch?.branchName || '主分支' }}
          </el-tag>
        </div>
      </template>
      <DynamicForm
        ref="dynamicFormRef"
        :fields="effectiveFields"
        :initial-data="currentRecord"
        :show-actions="false"
        @submit="handleSubmit"
      />
      <template #footer>
        <el-button @click="dialogVisible = false" :disabled="submitLoading">
          取消
        </el-button>
        <el-button
          type="primary"
          @click="handleFormSubmit"
          :loading="submitLoading"
        >
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 查看记录对话框 -->
    <el-dialog
      v-model="viewDialogVisible"
      width="700px"
      destroy-on-close
    >
      <template #header>
        <div style="display: flex; align-items: center;">
          <span style="font-weight: bold;">查看记录</span>
          <el-tag
            v-if="columnViewStore.currentView"
            type="info"
            size="small"
            style="margin-left: 8px;"
          >
            {{ columnViewStore.currentView.name }}
          </el-tag>
          <el-tag
            :type="currentBranch?.branchId ? 'primary' : 'success'"
            size="small"
            style="margin-left: auto;"
          >
            {{ currentBranch?.branchName || '主分支' }}
          </el-tag>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item
          v-for="field in viewDisplayFields"
          :key="field.id"
          :label="field.label"
          :label-width="140"
        >
          <!-- 关联关系字段：Tag 可点击跳转 -->
          <template v-if="field.controlType === 'relation'">
            <span v-if="!viewRecord[`_rel_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in viewRecord[`_rel_${field.fieldName}_labels`]"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click="viewDialogVisible = false; handleRelationClick(item.id, field)"
              >{{ item.label }}</el-tag>
            </span>
          </template>

          <!-- 引用选择字段：Tag 可点击跳转 -->
          <template v-else-if="field.controlType === 'quoteSelect'">
            <span v-if="!viewRecord[`_quote_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in viewRecord[`_quote_${field.fieldName}_labels`]"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click="viewDialogVisible = false; handleQuoteClick(item.id, field)"
              >{{ item.label }}</el-tag>
            </span>
          </template>

          <!-- 数据引用字段：可点击跳转 -->
          <template v-else-if="field.controlType === 'reference'">
            <span v-if="!viewRecord[field.fieldName]">-</span>
            <span
              v-else
              class="reference-link"
              @click="viewDialogVisible = false; handleReferenceClick(viewRecord as DynamicRecord, field)"
            >{{ viewRecord[`_ref_${field.fieldName}_display`] || viewRecord[field.fieldName] }}</span>
          </template>

          <!-- 选项类字段：显示标签 -->
          <template v-else-if="['select', 'radio'].includes(field.controlType)">
            {{ formatViewValue(field) }}
          </template>

          <!-- 多选类字段：Tag 展示 -->
          <template v-else-if="['multiSelect', 'checkbox'].includes(field.controlType)">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="v in viewRecord[field.fieldName]"
                :key="v"
                size="small"
              >{{ field.options?.find(o => o.value === v)?.label || v }}</el-tag>
            </span>
          </template>

          <!-- 文件/图片字段 -->
          <template v-else-if="field.controlType === 'file'">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <div v-else>
              <div v-for="(f, idx) in viewRecord[field.fieldName]" :key="idx">
                <el-link type="primary" :href="f.url" target="_blank">{{ f.name }}</el-link>
              </div>
            </div>
          </template>

          <template v-else-if="field.controlType === 'image'">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <div v-else class="view-images">
              <el-image
                v-for="(img, idx) in viewRecord[field.fieldName]"
                :key="idx"
                :src="img.url"
                :preview-src-list="viewRecord[field.fieldName].map((i: any) => i.url)"
                :initial-index="idx"
                fit="cover"
                class="view-image-item"
              />
            </div>
          </template>

          <!-- 日期/时间字段 -->
          <template v-else-if="['date', 'datetime', 'autoTimestamp'].includes(field.controlType)">
            {{ formatViewDate(viewRecord[field.fieldName], field.controlType) }}
          </template>

          <!-- 多行文本 -->
          <template v-else-if="field.controlType === 'textarea'">
            <span class="view-textarea">{{ viewRecord[field.fieldName] || '-' }}</span>
          </template>

          <!-- 富文本 -->
          <template v-else-if="field.controlType === 'richText'">
            <div class="view-richtext" v-html="viewRecord[field.fieldName] || '-'" />
          </template>

          <!-- 默认：纯文本 -->
          <template v-else>
            {{ viewRecord[field.fieldName] ?? '-' }}
          </template>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 评论/变更历史 -->
      <el-divider content-position="left">评论 / 变更历史</el-divider>
      <RecordTimeline
        v-if="viewDialogVisible && viewRecord.id"
        :collection="collection"
        :record-id="viewRecord.id"
      />

      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%">
          <WorkflowActions
            v-if="!isGuest"
            :record="viewRecord"
            :fields="pageFields"
            @transition="handleWorkflowTransition"
          />
          <span v-else></span>
          <div>
            <el-button @click="viewDialogVisible = false">关闭</el-button>
            <el-button type="info" @click="viewDialogVisible = false; handleShowRelationGraph(viewRecord as DynamicRecord)">
              关系图谱
            </el-button>
            <el-button v-if="!isGuest && canUpdate" type="primary" @click="viewDialogVisible = false; handleEdit(viewRecord as DynamicRecord)">
              编辑
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除这条记录吗？删除后无法恢复。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />

    <!-- 批量删除确认对话框 -->
    <ConfirmDialog
      v-model="batchDeleteDialogVisible"
      title="批量删除确认"
      :message="`确定要删除选中的 ${selectedRows.length} 条记录吗？删除后无法恢复。`"
      type="danger"
      confirm-text="全部删除"
      @confirm="handleBatchDelete"
    />

    <!-- 删除绑定对话框 -->
    <el-dialog
      v-model="deleteBindingDialogVisible"
      :title="pageConfig?.deleteBinding?.dialogTitle || '删除确认'"
      :width="pageConfig?.deleteBinding?.dialogWidth || '500px'"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #title>
          确定要删除此记录吗？删除后将自动创建相关记录。
        </template>
      </el-alert>

      <!-- 显示被删除记录的关键信息 -->
      <div v-if="deleteBindingRecord" class="deleted-record-info">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="记录ID">
            {{ deleteBindingRecord.id }}
          </el-descriptions-item>
          <el-descriptions-item
            v-for="field in deleteBindingInheritDisplayFields"
            :key="field.sourceField"
            :label="getDeleteBindingFieldLabel(field.sourceField)"
          >
            {{ formatDeleteBindingValue(deleteBindingRecord[field.sourceField], field.sourceField) }}
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 动态表单（使用目标集合的字段配置） -->
      <DynamicForm
        v-if="deleteBindingDialogVisible && deleteBindingFields.length > 0"
        ref="deleteBindingFormRef"
        :fields="deleteBindingFields"
        :initial-data="deleteBindingFormData"
        :show-actions="false"
        @submit="handleDeleteBindingFormSubmit"
      />

      <!-- 如果没有表单字段需要填写，显示提示 -->
      <el-alert
        v-else-if="deleteBindingFields.length === 0"
        type="info"
        :closable="false"
        show-icon
        style="margin-top: 16px"
      >
        <template #title>
          所有字段将自动继承，点击确认删除即可。
        </template>
      </el-alert>

      <template #footer>
        <el-button @click="deleteBindingDialogVisible = false" :disabled="deleteBindingLoading">
          取消
        </el-button>
        <el-button
          type="danger"
          @click="handleDeleteBindingSubmit"
          :loading="deleteBindingLoading"
        >
          确认删除
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量删除绑定对话框 -->
    <el-dialog
      v-model="batchDeleteBindingDialogVisible"
      :title="pageConfig?.deleteBinding?.dialogTitle || '批量删除确认'"
      :width="pageConfig?.deleteBinding?.dialogWidth || '500px'"
      :close-on-click-modal="false"
      :close-on-press-escape="!batchDeleteBindingLoading"
      :show-close="!batchDeleteBindingLoading"
      destroy-on-close
    >
      <!-- 进度显示 -->
      <div v-if="batchDeleteBindingLoading" class="batch-progress">
        <el-progress :percentage="batchDeleteBindingProgress" :stroke-width="20" striped striped-flow />
        <p>正在处理... {{ batchDeleteBindingCurrent }} / {{ batchDeleteBindingTotal }}</p>
      </div>

      <!-- 表单内容 -->
      <template v-else>
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        >
          <template #title>
            确定要删除选中的 {{ selectedRows.length }} 条记录吗？删除后将自动创建相关记录。
          </template>
        </el-alert>

        <!-- 动态表单（使用目标集合的字段配置） -->
        <DynamicForm
          v-if="deleteBindingFields.length > 0"
          ref="batchDeleteBindingFormRef"
          :fields="deleteBindingFields"
          :initial-data="batchDeleteBindingFormData"
          :show-actions="false"
          @submit="handleBatchDeleteBindingFormSubmit"
        />

        <!-- 如果没有表单字段需要填写，显示提示 -->
        <el-alert
          v-else
          type="info"
          :closable="false"
          show-icon
          style="margin-top: 16px"
        >
          <template #title>
            所有字段将自动继承，点击确认删除即可。
          </template>
        </el-alert>
      </template>

      <template #footer>
        <el-button
          @click="batchDeleteBindingDialogVisible = false"
          :disabled="batchDeleteBindingLoading"
        >
          取消
        </el-button>
        <el-button
          type="danger"
          @click="handleBatchDeleteBindingSubmit"
          :loading="batchDeleteBindingLoading"
          :disabled="batchDeleteBindingLoading"
        >
          确认删除全部
        </el-button>
      </template>
    </el-dialog>

    <!-- 隐藏的文件选择器 -->
    <input
      ref="fileInputRef"
      type="file"
      accept=".xlsx,.xls,.json"
      style="display: none"
      @change="handleFileSelected"
    />

    <!-- 导入进度对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入数据"
      width="450px"
      :close-on-click-modal="false"
      :close-on-press-escape="!importLoading"
      :show-close="!importLoading"
    >
      <div v-if="importLoading" class="import-progress">
        <el-progress :percentage="importProgress" :stroke-width="20" striped striped-flow />
        <p>正在导入... {{ importCurrent }} / {{ importTotal }}</p>
      </div>
      <div v-else-if="importResult" class="import-result">
        <el-result
          :icon="importResult.failed === 0 ? 'success' : 'warning'"
          :title="importResult.failed === 0 ? '导入完成' : '导入完成（部分失败）'"
        >
          <template #sub-title>
            <p>
              成功 {{ importResult.success }} 条
              <template v-if="importResult.updated">
                （新增 {{ importResult.created || 0 }} / 更新 {{ importResult.updated }}）
              </template>
              ,失败 {{ importResult.failed }} 条
            </p>
          </template>
        </el-result>
      </div>
      <template #footer>
        <el-button
          v-if="!importLoading"
          type="primary"
          @click="importDialogVisible = false"
        >
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 项目版本管理抽屉 -->
    <ProjectVersionManager
      v-if="projectMenuId"
      v-model="projectVersionManagerVisible"
      :project-menu-id="projectMenuId"
      :default-tab="projectVersionManagerDefaultTab"
      @refresh="loadPageData"
    />

    <!-- 关系图谱对话框 -->
    <RelationGraphDialog
      v-model="graphDialogVisible"
      :collection="collection"
      :record-id="graphRecordId"
      @navigate="handleGraphNodeNavigate"
      @updated="loadPageData"
    />

    <!-- 视图管理弹窗 -->
    <ViewManageDialog
      v-if="pageId"
      ref="viewManageDialogRef"
      :page-id="pageId"
      :fields="allExpandedFields"
      @edit-columns="handleEditColumns"
    />

    <!-- 列配置弹窗 -->
    <ColumnConfigDialog
      ref="columnConfigDialogRef"
      :fields="allExpandedFields"
      @save="handleColumnConfigSave"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * DynamicPage 组件脚本
 *
 * 路由参数：
 * - pageId: 页面配置ID
 *
 * 功能：
 * 1. 加载页面配置和数据
 * 2. 渲染数据表格
 * 3. 处理新增/编辑/删除操作
 */
import { ref, computed, watch, nextTick, onActivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Upload, Download, ArrowDown, Search, DCaret, Grid, Operation, MagicStick, Tickets, Document, Loading, Back, Check, Calendar, DataLine, RefreshRight, CopyDocument, QuestionFilled } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore, useAuthStore, useJumpNavigationStore, useColumnViewStore } from '@/stores'
import { DataTable, ConfirmDialog, RelationGraphDialog, KanbanBoard, RecordTimeline, WorkflowActions, ProjectVersionManager, ExcelView, CalendarView, GanttView } from '@/components/common'
import { DynamicForm } from '@/components/dynamic-form'
import { ViewSelector, ViewManageDialog, ColumnConfigDialog } from '@/components/column-view'
import { exportToExcel, generateImportTemplate, parseImportFile, parseJsonImportFile } from '@/utils/excel'
import { importPageRecords } from '@/utils/importPageRecords'
import { withBatch } from '@/utils/batch'
import { getExportScripts, executeExportScript } from '@/api/exportScript'
import { getCurrentProjectBranch, switchProjectBranch, listProjectVersions, switchToMainProjectBranch } from '@/api/projectVersion'
import type { CurrentBranch } from '@/api/projectVersion'
import type { ProjectVersion } from '@/types/version'
import { post } from '@/utils/request'
import type { PageConfig, FieldConfig, DynamicRecord, ExportScript, KanbanConfig, FieldOption, DeleteBindingConfig, CalendarConfig, GanttConfig } from '@/types'
import { searchModeTransition, type SearchMode } from './searchMode'

// ==================== Props ====================

const props = defineProps<{
  pageId?: string
}>()

// ==================== Route & Store ====================

const route = useRoute()
const router = useRouter()
const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()
const authStore = useAuthStore()
const jumpStore = useJumpNavigationStore()
const columnViewStore = useColumnViewStore()
const isAdmin = computed(() => authStore.isAdmin)
const isGuest = computed(() => authStore.isGuest)

// ==================== 页面级 CRUD 权限（仅控制按钮显隐，服务端为权威） ====================
// pageId 已是 `page-<collection>` 形式，canPage 接收完整 pageId
const canCreate = computed(() => authStore.canPage(pageId.value, 'create'))
const canUpdate = computed(() => authStore.canPage(pageId.value, 'update'))
const canDelete = computed(() => authStore.canPage(pageId.value, 'delete'))

// ==================== Refs ====================

/**
 * 动态表单引用
 */
const dynamicFormRef = ref<InstanceType<typeof DynamicForm>>()

/**
 * 数据表格引用
 */
const dataTableRef = ref<InstanceType<typeof DataTable>>()

/**
 * 文件选择器引用
 */
const fileInputRef = ref<HTMLInputElement>()

// ==================== State ====================

/**
 * 页面加载状态
 */
const pageLoading = ref(false)

/**
 * 表格加载状态
 */
const tableLoading = ref(false)

/**
 * 提交加载状态
 */
const submitLoading = ref(false)

/**
 * 表格数据
 */
const tableData = ref<DynamicRecord[]>([])

/**
 * 数据总数（后端分页）
 */
const totalCount = ref(0)

/**
 * 搜索关键字
 */
const searchKeyword = ref('')

/**
 * 关键字搜索的防抖定时器
 */
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 列筛选条件
 */
const columnFilters = ref<Record<string, { value: any; value2?: any; operator?: string }>>({})

/**
 * 统一检索模式（关键字 / AI / 高级）。
 * 模板与逻辑统一以此为单一来源，不再保留 aiSearchMode/queryMode 两布尔。
 */
const searchMode = ref<SearchMode>('keyword')

/**
 * 高级查询模式
 */
const mongoQueryText = ref('')
const activeMongoQuery = ref<Record<string, any> | null>(null)

/**
 * AI 搜索模式
 */
const aiSearchText = ref('')
const aiSearchLoading = ref(false)
const aiGeneratedFilter = ref<Record<string, any> | null>(null)

/**
 * 对话框可见性
 */
const dialogVisible = ref(false)

/**
 * 查看对话框可见性
 */
const viewDialogVisible = ref(false)

/**
 * 删除对话框可见性
 */
const deleteDialogVisible = ref(false)

/**
 * 当前编辑的记录
 */
const currentRecord = ref<Record<string, any>>({})

/**
 * 当前查看的记录
 */
const viewRecord = ref<Record<string, any>>({})

/**
 * 待删除的记录ID
 */
const deleteRecordId = ref<string>('')

/**
 * 批量删除对话框可见性
 */
const batchDeleteDialogVisible = ref(false)

/**
 * 删除绑定对话框可见性
 */
const deleteBindingDialogVisible = ref(false)

/**
 * 待删除绑定的记录
 */
const deleteBindingRecord = ref<DynamicRecord | null>(null)

/**
 * 删除绑定表单数据
 */
const deleteBindingFormData = ref<Record<string, any>>({})

/**
 * 删除绑定表单引用
 */
const deleteBindingFormRef = ref<InstanceType<typeof DynamicForm>>()

/**
 * 删除绑定提交加载状态
 */
const deleteBindingLoading = ref(false)

/**
 * 批量删除绑定对话框可见性
 */
const batchDeleteBindingDialogVisible = ref(false)

/**
 * 批量删除绑定表单数据
 */
const batchDeleteBindingFormData = ref<Record<string, any>>({})

/**
 * 批量删除绑定表单引用
 */
const batchDeleteBindingFormRef = ref<InstanceType<typeof DynamicForm>>()

/**
 * 批量删除绑定提交加载状态
 */
const batchDeleteBindingLoading = ref(false)

/**
 * 批量删除绑定进度
 */
const batchDeleteBindingProgress = ref(0)
const batchDeleteBindingCurrent = ref(0)
const batchDeleteBindingTotal = ref(0)

/**
 * 当前选中的行
 */
const selectedRows = ref<DynamicRecord[]>([])

/**
 * 是否编辑模式
 */
const isEditMode = ref(false)

/**
 * 是否复制新增模式（区别于空白新增和编辑）
 */
const isCopyMode = ref(false)

/**
 * 导入对话框可见性
 */
const importDialogVisible = ref(false)

/**
 * 导入加载状态
 */
const importLoading = ref(false)

/**
 * 导入进度
 */
const importProgress = ref(0)
const importCurrent = ref(0)
const importTotal = ref(0)

/**
 * 导入结果
 */
const importResult = ref<{ success: number; failed: number; created?: number; updated?: number } | null>(null)

/**
 * 所有导出脚本（缓存）
 */
const allExportScripts = ref<ExportScript[]>([])

/**
 * 项目版本管理抽屉可见性
 */
const projectVersionManagerVisible = ref(false)

/**
 * 项目版本管理默认打开的Tab
 */
const projectVersionManagerDefaultTab = ref<'versions' | 'dependencies'>('versions')

/**
 * 当前菜单（通过pageId查找）
 */
const currentMenu = computed(() => {
  return menuStore.menuList.find(m => m.pageId === pageId.value)
})

/**
 * 当前数据菜单所属的项目菜单ID
 * 优先使用 projectId，如果没有则使用 parentId（数据菜单的父级就是项目菜单）
 */
const projectMenuId = computed(() => {
  // 如果 projectId 有值，直接使用
  if (currentMenu.value?.projectId) {
    return currentMenu.value.projectId
  }
  // 如果是数据菜单且 parentId 有值，使用 parentId（父级就是项目菜单）
  if (currentMenu.value?.menuType === 'data' && currentMenu.value?.parentId) {
    return currentMenu.value.parentId
  }
  return null
})

/**
 * 当前用户分支信息
 */
const currentBranch = ref<CurrentBranch | null>(null)

/**
 * 分支列表（用于切换下拉菜单）
 */
const branchVersions = ref<ProjectVersion[]>([])

/**
 * 分支切换下拉菜单可见性
 */
const _showBranchDropdown = ref(false)

// Acknowledge unused variable (will be used in future tasks)
void _showBranchDropdown

/**
 * 分支切换加载状态
 */
const branchSwitching = ref(false)

/**
 * 关系图谱对话框可见性
 */
const graphDialogVisible = ref(false)

/**
 * 关系图谱记录ID
 */
const graphRecordId = ref('')

/**
 * 视图管理弹窗引用
 */
const viewManageDialogRef = ref<InstanceType<typeof ViewManageDialog>>()

/**
 * 列配置弹窗引用
 */
const columnConfigDialogRef = ref<InstanceType<typeof ColumnConfigDialog>>()

/**
 * 正在编辑列配置的视图 id（来自视图管理弹窗的选中项，可能不是当前应用的视图）
 */
const editingViewId = ref<number | null>(null)

/**
 * 视图模式（table / kanban / excel / calendar / gantt）
 */
const viewMode = ref<'table' | 'kanban' | 'excel' | 'calendar' | 'gantt'>('table')

/** Excel 视图组件引用 */
const excelViewRef = ref<{ saveSnapshot: () => void } | null>(null)
const calendarViewRef = ref<{ getApi: () => any } | null>(null)

/**
 * Excel 视图延迟加载状态
 * 用于在切换到 Excel 视图时避免立即渲染大量数据导致的卡顿
 */
const excelReady = ref(false)

/** Excel 视图是否已初始化（用于延迟挂载） */
const excelInitialized = ref(false)

/** Excel 视图全量数据 */
const excelData = ref<DynamicRecord[]>([])

/** Excel 视图数据加载状态 */
const excelLoading = ref(false)

/**
 * 分页状态
 */
const currentPage = ref(1)
const currentPageSize = ref(50)

// ==================== 计算属性 ====================

/**
 * 当前页面ID
 */
const pageId = computed(() => props.pageId || (route.params.pageId as string))

/**
 * 集合名称（用于对比接口）
 */
const collection = computed(() => pageId.value.replace('page-', ''))

/**
 * 页面配置
 */
const pageConfig = computed<PageConfig | undefined>(() => {
  return pageConfigStore.getPageConfigById(pageId.value)
})

/**
 * 页面字段配置
 */
const pageFields = computed<FieldConfig[]>(() => {
  return pageConfigStore.getPageFields(pageId.value)
})

const hasReferenceFields = computed<boolean>(() =>
  (pageConfig.value?.fields ?? []).some((f) => f.controlType === 'quoteSelect' || f.controlType === 'reference'),
)

/**
 * 看板配置
 */
const kanbanConfig = computed<KanbanConfig | undefined>(() => {
  return pageConfig.value?.viewConfig?.kanban
})

const hasKanbanConfig = computed(() => {
  return !!kanbanConfig.value?.groupField
})

const calendarConfig = computed<CalendarConfig | undefined>(() => {
  return pageConfig.value?.viewConfig?.calendar
})

const hasCalendarConfig = computed(() => {
  if (!calendarConfig.value) return false
  const dateField = pageFields.value.find(f => f.fieldName === calendarConfig.value!.dateField)
  return dateField && ['date', 'datetime'].includes(dateField.controlType)
})

const ganttConfig = computed<GanttConfig | undefined>(() => {
  return pageConfig.value?.viewConfig?.gantt
})

const hasGanttConfig = computed(() => {
  if (!ganttConfig.value) return false
  const startField = pageFields.value.find(f => f.fieldName === ganttConfig.value!.startDateField)
  const endField = pageFields.value.find(f => f.fieldName === ganttConfig.value!.endDateField)
  return startField && endField &&
    ['date', 'datetime'].includes(startField.controlType) &&
    ['date', 'datetime'].includes(endField.controlType)
})

const kanbanGroupOptions = computed<FieldOption[]>(() => {
  if (!kanbanConfig.value) return []
  const groupFieldConfig = pageFields.value.find(f => f.fieldName === kanbanConfig.value!.groupField)
  return groupFieldConfig?.options || []
})

/**
 * 当前页面绑定的导出脚本
 */
const boundExportScripts = computed<ExportScript[]>(() => {
  const ids = pageConfig.value?.exportScripts || []
  if (ids.length === 0) return []
  return allExportScripts.value.filter(s => ids.includes(s.id))
})

/**
 * 当前页面绑定的行级导出脚本
 */
const boundRowExportScripts = computed<ExportScript[]>(() => {
  const ids = pageConfig.value?.rowExportScripts || []
  if (ids.length === 0) return []
  return allExportScripts.value.filter(s => ids.includes(s.id))
})

/**
 * 全量展开字段列表（含引用字段展开的继承虚拟列，不做视图过滤）
 * 用于列配置弹窗、视图管理弹窗等需要展示所有字段的场景
 */
const allExpandedFields = computed<FieldConfig[]>(() => {
  const result: FieldConfig[] = []
  for (const field of pageFields.value) {
    result.push(field)
    if (field.controlType === 'reference' && field.referenceConfig?.inheritFields?.length) {
      const config = field.referenceConfig
      const targetPageConfig = pageConfigStore.getPageConfigById(`page-${config.targetCollection}`)
      const targetFields = targetPageConfig?.fields || []
      for (const inheritFieldName of config.inheritFields) {
        const parentField = targetFields.find((f) => f.fieldName === inheritFieldName)
        result.push({
          id: `_ref_${field.fieldName}_${inheritFieldName}`,
          fieldName: `_ref_${field.fieldName}_${inheritFieldName}`,
          label: parentField?.label || inheritFieldName,
          controlType: parentField?.controlType || 'text',
          required: false,
          order: field.order + 0.1,
          hidden: false,
          disabled: true,
          options: parentField?.options
        })
      }
    }
  }
  return result
})

/**
 * 有效字段列表（含引用字段展开的继承虚拟列）
 * 用于 DataTable / 查看 / 编辑弹窗显示
 * 如果选择了列视图，则应用视图的列配置（过滤、排序、宽度）
 */
const effectiveFields = computed<FieldConfig[]>(() => {
  return columnViewStore.getTableColumns(allExpandedFields.value)
})

/**
 * 对话框标题
 */
const dialogTitle = computed(() => {
  if (isEditMode.value) return '编辑记录'
  if (isCopyMode.value) return '复制新增'
  return '新增记录'
})

/**
 * 查看对话框中显示的字段列表
 * 当选择了列视图时，只显示视图中可见的字段；否则显示所有非隐藏字段
 */
const viewDisplayFields = computed<FieldConfig[]>(() => {
  // 使用 effectiveFields（已含列视图过滤和 reference 继承字段展开）
  return effectiveFields.value
})

/**
 * 表格显示数据（后端分页，直接使用 tableData）
 * 注：关键字搜索已由后端处理，此处仅处理列筛选（当前页内生效）
 */
const filteredData = computed<DynamicRecord[]>(() => {
  let result = tableData.value

  // 关键字搜索已由后端处理（keyword 参数），此处仅处理列筛选
  const filterEntries = Object.entries(columnFilters.value)
  if (filterEntries.length > 0) {
    result = result.filter(record => {
      return filterEntries.every(([fieldName, filter]) => {
        const field = pageFields.value.find(f => f.fieldName === fieldName)
        if (!field) return true

        if (field.controlType === 'relation') {
          const labels = record[`_rel_${fieldName}_labels`]
          if (!Array.isArray(labels) || labels.length === 0) return false
          const keyword = String(filter.value).toLowerCase()
          return labels.some((item: { id: string; label: string }) =>
            item.label.toLowerCase().includes(keyword)
          )
        }

        if (field.controlType === 'quoteSelect') {
          const labels = record[`_quote_${fieldName}_labels`]
          if (!Array.isArray(labels) || labels.length === 0) return false
          const keyword = String(filter.value).toLowerCase()
          return labels.some((item: { id: string; label: string }) =>
            item.label.toLowerCase().includes(keyword)
          )
        }

        if (field.controlType === 'reference') {
          const display = record[`_ref_${fieldName}_display`]
          if (!display) return false
          const keyword = String(filter.value).toLowerCase()
          return String(display).toLowerCase().includes(keyword)
        }

        const val = record[fieldName]
        if (val === null || val === undefined || val === '') return false

        if (['select', 'radio'].includes(field.controlType)) {
          return val === filter.value
        }

        if (['multiSelect', 'checkbox'].includes(field.controlType)) {
          if (!Array.isArray(val) || !Array.isArray(filter.value)) return false
          return filter.value.every(v => val.includes(v))
        }

        if (['date', 'datetime', 'autoTimestamp'].includes(field.controlType)) {
          const recordDate = new Date(val).getTime()
          const filterDate = filter.value ? new Date(filter.value).getTime() : null
          const filterDate2 = filter.value2 ? new Date(filter.value2).getTime() : null

          if (filter.operator === 'eq' && filterDate) {
            return recordDate === filterDate
          } else if (filter.operator === 'lt' && filterDate) {
            return recordDate < filterDate
          } else if (filter.operator === 'gt' && filterDate) {
            return recordDate > filterDate
          } else if (filter.operator === 'between' && filterDate && filterDate2) {
            return recordDate >= filterDate && recordDate <= filterDate2
          }
          return true
        }

        if (field.controlType === 'number') {
          const numVal = Number(val)
          const numFilter = Number(filter.value)
          const numFilter2 = Number(filter.value2)

          if (filter.operator === 'eq') {
            return numVal === numFilter
          } else if (filter.operator === 'gt') {
            return numVal > numFilter
          } else if (filter.operator === 'lt') {
            return numVal < numFilter
          } else if (filter.operator === 'between') {
            return numVal >= numFilter && numVal <= numFilter2
          }
          return true
        }

        const strVal = String(val).toLowerCase()
        const strFilter = String(filter.value).toLowerCase()
        return strVal.includes(strFilter)
      })
    })
  }

  return result
})

/**
 * 分页后的数据（后端分页，直接使用 filteredData）
 */
const paginatedData = computed<DynamicRecord[]>(() => {
  return filteredData.value
})

/**
 * 删除绑定配置
 */
const deleteBindingConfig = computed<DeleteBindingConfig | undefined>(() => {
  return pageConfig.value?.deleteBinding
})

/**
 * 删除绑定表单字段（直接使用目标集合的字段配置）
 */
const deleteBindingFields = computed<FieldConfig[]>(() => {
  if (!deleteBindingConfig.value?.targetCollection) return []

  // 获取目标集合的字段配置
  const targetPageId = `page-${deleteBindingConfig.value.targetCollection}`
  const targetConfig = pageConfigStore.getPageConfigById(targetPageId)

  if (!targetConfig?.fields) return []

  // 过滤掉继承字段（继承字段会自动填充，不需要用户填写）
  const inheritTargetFields = new Set(
    deleteBindingConfig.value.inheritFields?.map(m => m.targetField) || []
  )

  // 过滤掉系统自动填充字段
  const systemFields = new Set(['_operatorName', '_operatorUsername', '_deletedAt', '_sourceRecordId', '_sourceCollection'])

  return targetConfig.fields.filter(f =>
    !inheritTargetFields.has(f.fieldName) &&
    !systemFields.has(f.fieldName)
  )
})

/**
 * 删除绑定继承字段显示列表（去重，最多显示5个）
 */
const deleteBindingInheritDisplayFields = computed<{ sourceField: string; targetField: string }[]>(() => {
  const inheritFields = deleteBindingConfig.value?.inheritFields || []
  return inheritFields.slice(0, 5)
})

// ==================== 方法 ====================

/**
 * 处理分页变化（后端分页）
 */
async function handlePageChange(page: number, pageSize: number): Promise<void> {
  currentPage.value = page
  currentPageSize.value = pageSize
  await loadPageData()
}

/**
 * 处理列筛选变化
 */
function handleFilterChange(filters: Record<string, { value: any; value2?: any; operator?: string }>): void {
  columnFilters.value = filters
  currentPage.value = 1
}

/**
 * 格式化查看对话框中的选项字段值
 */
function formatViewValue(field: FieldConfig): string {
  const value = viewRecord.value[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}

/**
 * 格式化查看对话框中的日期值
 */
function formatViewDate(value: any, controlType: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return String(value)
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    if (controlType === 'date') return `${y}-${m}-${d}`
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
  } catch {
    return String(value)
  }
}

/**
 * 加载页面数据（后端分页）
 */
async function loadPageData(): Promise<void> {
  if (!pageId.value) return

  // 按需加载 page_configs（首次访问动态页时触发）
  if (!pageConfigStore.pageConfigs.length) {
    await pageConfigStore.fetchPageConfigs()
  }

  tableLoading.value = true
  try {
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      page: currentPage.value,
      pageSize: currentPageSize.value,
      keyword: searchKeyword.value || undefined
    })
    tableData.value = result.data
    totalCount.value = result.total
    // 加载当前分支信息
    await loadCurrentBranch()
  } catch (error) {
    console.error('加载数据失败:', error)
    ElMessage.error('加载数据失败')
  } finally {
    tableLoading.value = false
  }
}

/**
 * 加载当前用户分支信息
 */
async function loadCurrentBranch(): Promise<void> {
  // 如果没有项目菜单ID，显示主分支状态
  if (!projectMenuId.value) {
    currentBranch.value = {
      branchId: '',
      branchName: '主分支'
    }
    return
  }

  try {
    const projectBranch = await getCurrentProjectBranch(projectMenuId.value)
    currentBranch.value = {
      branchId: projectBranch.branchId === 'main' ? '' : projectBranch.branchId,
      branchName: projectBranch.branchName
    }
  } catch (error) {
    console.error('获取分支信息失败:', error)
    // 获取失败时也显示主分支
    currentBranch.value = {
      branchId: '',
      branchName: '主分支'
    }
  }
}

/**
 * 加载分支列表（用于切换下拉菜单）
 */
async function loadBranchVersions(): Promise<void> {
  if (!projectMenuId.value) return

  try {
    const result = await listProjectVersions(projectMenuId.value, 1, 50)
    // 只筛选分支类型且状态为 active
    branchVersions.value = result.items.filter(v => v.versionType === 'branch' && v.status === 'active')
  } catch (error) {
    console.error('获取分支列表失败:', error)
    branchVersions.value = []
  }
}

/**
 * 处理分支切换
 */
async function handleBranchSwitch(command: string): Promise<void> {
  if (command === 'manage') {
    // 如果数据菜单属于项目，使用项目版本管理
    if (projectMenuId.value) {
      projectVersionManagerDefaultTab.value = 'versions'
      projectVersionManagerVisible.value = true
    } else {
      ElMessage.warning('该数据页不属于任何项目，无法使用版本管理')
    }
    return
  }

  // 如果属于项目，分支切换需要通过项目版本管理
  if (projectMenuId.value) {
    if (command === 'main') {
      // TODO: 实现项目分支切换到 main
      ElMessage.warning('项目分支切换请在项目版本管理中进行')
      return
    }
    try {
      const result = await switchProjectBranch(command, projectMenuId.value)
      await loadCurrentBranch()
      await loadPageData()
      ElMessage.success(`已切换到分支「${result.branchName}」`)
    } catch (error: any) {
      const msg = error?.response?.data?.error || '切换失败'
      ElMessage.error(msg)
    }
    return
  }

  if (command === 'main') {
    // 切换到主分支
    branchSwitching.value = true
    try {
      await switchToMainProjectBranch(projectMenuId.value!)
      await loadCurrentBranch()
      await loadPageData()
      ElMessage.success('已切换到主分支')
    } catch (error: any) {
      const msg = error?.response?.data?.error || '切换失败'
      ElMessage.error(msg)
    } finally {
      branchSwitching.value = false
    }
    return
  }

  // 切换到指定分支
  branchSwitching.value = true
  try {
    const result = await switchProjectBranch(command, projectMenuId.value!)
    await loadCurrentBranch()
    await loadPageData()

    ElMessage.success(`已切换到分支「${result.branchName}」`)
  } catch (error: any) {
    const msg = error?.response?.data?.error || '切换失败'
    ElMessage.error(msg)
  } finally {
    branchSwitching.value = false
  }
}

/**
 * 加载 Excel 视图全量数据
 * Excel 视图需要展示所有数据，不使用分页
 */
async function loadExcelData(): Promise<void> {
  if (!pageId.value || excelLoading.value) return

  excelLoading.value = true
  try {
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      keyword: searchKeyword.value || undefined,
      loadAll: true // 加载全量数据，不受 pageSize 限制
    })
    excelData.value = result.data
  } catch (error) {
    console.error('加载 Excel 数据失败:', error)
    ElMessage.error('加载 Excel 数据失败')
  } finally {
    excelLoading.value = false
  }
}

/**
 * 切换统一检索模式（关键字 / AI / 高级）。
 * 互斥副作用复用纯函数 searchModeTransition。
 */
function setSearchMode(mode: SearchMode): void {
  const from = searchMode.value
  if (from === mode) return
  const { clearAi, clearMongo } = searchModeTransition(from, mode, {
    hasAiFilter: !!aiGeneratedFilter.value,
    hasMongoQuery: !!activeMongoQuery.value,
  })
  searchMode.value = mode
  if (clearAi) clearAiQuery()
  if (clearMongo) clearMongoQuery()
}

/**
 * 执行 MongoDB 查询
 */
async function executeMongoQuery(): Promise<void> {
  const text = mongoQueryText.value.trim()
  if (!text) {
    clearMongoQuery()
    return
  }
  try {
    const query = JSON.parse(text)
    if (typeof query !== 'object' || Array.isArray(query)) {
      ElMessage.warning('查询必须是 JSON 对象，如 {"field": "value"}')
      return
    }
    activeMongoQuery.value = query
    currentPage.value = 1
    await loadPageData()
    // 如果当前是 Excel 视图，同时刷新全量数据
    if (viewMode.value === 'excel') {
      await loadExcelData()
    }
  } catch (e: any) {
    if (e instanceof SyntaxError) {
      ElMessage.error('JSON 格式错误: ' + e.message)
    } else {
      ElMessage.error(e.message || '查询失败')
    }
  }
}

/**
 * 清除查询
 */
async function clearMongoQuery(): Promise<void> {
  activeMongoQuery.value = null
  mongoQueryText.value = ''
  currentPage.value = 1
  await loadPageData()
  // 如果当前是 Excel 视图，同时刷新全量数据
  if (viewMode.value === 'excel') {
    await loadExcelData()
  }
}

/**
 * 执行 AI 自然语言查询
 */
async function executeAiQuery(): Promise<void> {
  const text = aiSearchText.value.trim()
  if (!text) return

  const collection = pageId.value?.replace('page-', '')
  if (!collection) return

  aiSearchLoading.value = true
  try {
    const result = await post<{ filter: Record<string, any> }>('/ai/query', {
      collection,
      question: text,
    })
    const filter = result.filter
    if (!filter || Object.keys(filter).length === 0) {
      ElMessage.warning('AI 未能生成有效的查询条件，请尝试更具体的描述')
      return
    }
    aiGeneratedFilter.value = filter
    activeMongoQuery.value = filter
    currentPage.value = 1
    await loadPageData()
    // 如果当前是 Excel 视图，同时刷新全量数据
    if (viewMode.value === 'excel') {
      await loadExcelData()
    }
    ElMessage.success('AI 查询已应用')
  } catch (e: any) {
    ElMessage.error(e.message || e.error || 'AI 查询失败')
  } finally {
    aiSearchLoading.value = false
  }
}

/**
 * 清除 AI 查询
 */
async function clearAiQuery(): Promise<void> {
  aiGeneratedFilter.value = null
  aiSearchText.value = ''
  activeMongoQuery.value = null
  currentPage.value = 1
  await loadPageData()
  // 如果当前是 Excel 视图，同时刷新全量数据
  if (viewMode.value === 'excel') {
    await loadExcelData()
  }
}

/**
 * 处理查看记录
 */
function handleView(row: DynamicRecord): void {
  viewRecord.value = { ...row }
  viewDialogVisible.value = true
}

/**
 * 处理工作流状态转换（从查看弹窗中的快捷按钮）
 */
async function handleWorkflowTransition(payload: { field: string; from: string; to: string }): Promise<void> {
  const record = viewRecord.value
  if (!record?.id) return
  try {
    const updateData: Record<string, any> = { ...record }
    updateData[payload.field] = payload.to
    delete updateData.id
    delete updateData.createdAt
    delete updateData.updatedAt
    for (const key of Object.keys(updateData)) {
      if (key.startsWith('_rel_') || key.startsWith('_ref_') || key.startsWith('_quote_')) {
        delete updateData[key]
      }
    }
    await pageConfigStore.updatePageData(pageId.value, record.id, updateData)
    ElMessage.success('状态已更新')
    // Update local state
    const row = tableData.value.find(r => r.id === record.id)
    if (row) {
      row[payload.field] = payload.to
      tableData.value = [...tableData.value]
    }
    viewRecord.value = { ...viewRecord.value, [payload.field]: payload.to }
  } catch (error: any) {
    const resp = error.response?.data
    ElMessage.error(resp?.error || '状态更新失败')
  }
}

/**
 * 处理看板卡片拖拽移动
 */
async function handleKanbanCardMove(recordId: string, newGroupValue: string): Promise<void> {
  if (!kanbanConfig.value) return
  const record = tableData.value.find(r => r.id === recordId)
  if (!record) return
  try {
    const updateData: Record<string, any> = { ...record }
    updateData[kanbanConfig.value.groupField] = newGroupValue
    // Strip internal fields
    delete updateData.id
    delete updateData.createdAt
    delete updateData.updatedAt
    for (const key of Object.keys(updateData)) {
      if (key.startsWith('_rel_') || key.startsWith('_ref_') || key.startsWith('_quote_')) {
        delete updateData[key]
      }
    }
    await pageConfigStore.updatePageData(pageId.value, recordId, updateData)
    // Update local data
    record[kanbanConfig.value.groupField] = newGroupValue
    tableData.value = [...tableData.value]
    ElMessage.success('状态已更新')
  } catch (error: any) {
    const resp = error.response?.data
    if (resp?.error) {
      ElMessage.error(resp.error)
    } else {
      ElMessage.error('状态更新失败')
    }
    // Reload to revert kanban state
    await loadPageData()
  }
}

async function handleCalendarDateChange(payload: { recordId: string; updates: Record<string, any> }): Promise<void> {
  try {
    await pageConfigStore.updatePageData(pageId.value, payload.recordId, payload.updates)
    ElMessage.success('日期已更新')
    await loadPageData()
  } catch (error: any) {
    const resp = error.response?.data
    ElMessage.error(resp?.error || '日期更新失败')
    await loadPageData()
  }
}

function handleCalendarDateClick(date: Date): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = false
  // 使用本地日期而非 UTC 日期，避免时区导致日期偏移一天
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const dateStr = `${year}-${month}-${day}`
  currentRecord.value = {
    [calendarConfig.value!.dateField]: dateStr
  }
  dialogVisible.value = true
}

/**
 * 处理新增
 */
function handleAdd(): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = false
  isCopyMode.value = false
  currentRecord.value = {}
  dialogVisible.value = true
}

/**
 * 处理编辑
 */
function handleEdit(row: DynamicRecord): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = true
  isCopyMode.value = false
  currentRecord.value = { ...row }
  dialogVisible.value = true
}

/**
 * 处理复制新增：预填充行数据，去掉自动生成字段，以新增模式打开表单
 */
function handleCopy(row: DynamicRecord): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  const config = pageConfigStore.pageConfigs.find(c => c.id === pageId.value)
  const autoFields = new Set(
    (config?.fields ?? [])
      .filter(f => f.controlType === 'autoSequence' || f.controlType === 'autoTimestamp')
      .map(f => f.fieldName)
  )
  const copied: Record<string, any> = {}
  for (const [k, v] of Object.entries(row)) {
    if (k === 'id' || autoFields.has(k)) continue
    copied[k] = v
  }
  isEditMode.value = false
  isCopyMode.value = true
  currentRecord.value = copied
  dialogVisible.value = true
}

/**
 * 处理删除确认
 */
function handleDeleteConfirm(row: DynamicRecord): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }

  // 检测是否启用删除绑定
  if (pageConfig.value?.deleteBinding?.enabled) {
    // 显示删除绑定表单对话框
    deleteBindingRecord.value = row
    deleteBindingFormData.value = {}
    deleteBindingDialogVisible.value = true
  } else {
    // 显示普通确认对话框
    deleteRecordId.value = row.id
    deleteDialogVisible.value = true
  }
}

/**
 * 处理删除
 */
async function handleDelete(): Promise<void> {
  if (!deleteRecordId.value) return

  try {
    await pageConfigStore.deletePageData(pageId.value, deleteRecordId.value)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    // 刷新数据
    await loadPageData()
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

/**
 * 处理删除绑定表单提交
 */
async function handleDeleteBindingSubmit(): Promise<void> {
  const config = pageConfig.value?.deleteBinding
  if (!config || !deleteBindingRecord.value) return

  // 验证表单（如果有表单字段）
  if (deleteBindingFields.value.length > 0 && deleteBindingFormRef.value) {
    const isValid = await deleteBindingFormRef.value.validate()
    if (!isValid) return
  }

  // 获取表单数据（如果有表单）
  const formData = deleteBindingFormRef.value?.getFormData() || {}
  const targetData: Record<string, any> = { ...formData }

  // 1. 填充继承字段
  for (const mapping of config.inheritFields) {
    targetData[mapping.targetField] = deleteBindingRecord.value[mapping.sourceField]
  }

  // 2. 自动填充操作者信息
  if (config.autoFillOperator) {
    targetData._operatorName = authStore.user?.displayName || authStore.user?.username || ''
    targetData._operatorUsername = authStore.user?.username || ''
    targetData._deletedAt = new Date().toISOString()
  }

  // 3. 记录被删除记录信息
  targetData._sourceRecordId = deleteBindingRecord.value.id
  targetData._sourceCollection = collection.value

  deleteBindingLoading.value = true
  try {
    // 4. 先创建目标记录
    await pageConfigStore.addPageData(`page-${config.targetCollection}`, targetData)

    // 5. 再删除原记录
    await pageConfigStore.deletePageData(pageId.value, deleteBindingRecord.value.id)

    ElMessage.success('操作成功')
    deleteBindingDialogVisible.value = false
    deleteBindingRecord.value = null
    deleteBindingFormData.value = {}

    // 刷新数据
    await loadPageData()
  } catch (error: any) {
    const resp = error.response?.data
    if (resp?.error) {
      ElMessage.error(`操作失败: ${resp.error}`)
    } else {
      ElMessage.error('操作失败')
    }
  } finally {
    deleteBindingLoading.value = false
  }
}

/**
 * 处理删除绑定表单提交事件
 */
async function handleDeleteBindingFormSubmit(data: Record<string, any>): Promise<void> {
  deleteBindingFormData.value = data
  await handleDeleteBindingSubmit()
}

/**
 * 获取删除绑定字段标签
 */
function getDeleteBindingFieldLabel(fieldName: string): string {
  const field = pageFields.value.find(f => f.fieldName === fieldName)
  return field?.label || fieldName
}

/**
 * 格式化删除绑定字段值
 */
function formatDeleteBindingValue(value: any, fieldName: string): string {
  if (value === null || value === undefined || value === '') return '-'

  const field = pageFields.value.find(f => f.fieldName === fieldName)
  if (!field) return String(value)

  // 选项类字段
  if (['select', 'radio'].includes(field.controlType)) {
    const opt = field.options?.find(o => o.value === value)
    return opt?.label || String(value)
  }

  // 多选类字段
  if (['multiSelect', 'checkbox'].includes(field.controlType)) {
    if (Array.isArray(value)) {
      return value.map(v => {
        const opt = field.options?.find(o => o.value === v)
        return opt?.label || String(v)
      }).join(', ')
    }
  }

  // 日期时间字段
  if (['date', 'datetime', 'autoTimestamp'].includes(field.controlType)) {
    try {
      const date = new Date(value)
      if (isNaN(date.getTime())) return String(value)
      const y = date.getFullYear()
      const m = String(date.getMonth() + 1).padStart(2, '0')
      const d = String(date.getDate()).padStart(2, '0')
      if (field.controlType === 'date') return `${y}-${m}-${d}`
      const hh = String(date.getHours()).padStart(2, '0')
      const mm = String(date.getMinutes()).padStart(2, '0')
      return `${y}-${m}-${d} ${hh}:${mm}`
    } catch {
      return String(value)
    }
  }

  return String(value)
}

/**
 * 处理多选变化
 */
function handleSelectionChange(rows: DynamicRecord[]): void {
  selectedRows.value = rows
}

/**
 * 处理批量删除确认
 */
function handleBatchDeleteConfirm(): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  if (selectedRows.value.length === 0) {
    ElMessage.warning('请先勾选要删除的记录')
    return
  }

  // 检测是否启用删除绑定
  if (pageConfig.value?.deleteBinding?.enabled) {
    // 显示批量删除绑定表单对话框
    batchDeleteBindingFormData.value = {}
    batchDeleteBindingProgress.value = 0
    batchDeleteBindingCurrent.value = 0
    batchDeleteBindingTotal.value = selectedRows.value.length
    batchDeleteBindingDialogVisible.value = true
  } else {
    // 显示普通批量删除确认对话框
    batchDeleteDialogVisible.value = true
  }
}

/**
 * 执行批量删除
 */
async function handleBatchDelete(): Promise<void> {
  const rows = [...selectedRows.value]
  batchDeleteDialogVisible.value = false

  try {
    const ids = rows.map((r) => r.id)
    const result = await pageConfigStore.batchDeletePageData(pageId.value, ids)

    selectedRows.value = []
    dataTableRef.value?.clearSelection()

    const blockedCount = result.blocked ? Object.keys(result.blocked).length : 0
    if (blockedCount === 0) {
      ElMessage.success(`成功删除 ${result.deleted} 条记录`)
    } else {
      ElMessage.warning(`删除完成：成功 ${result.deleted} 条，${blockedCount} 条因被引用跳过`)
    }

    await loadPageData()
  } catch {
    ElMessage.error('批量删除失败')
  }
}

/**
 * 处理批量删除绑定表单提交
 */
async function handleBatchDeleteBindingSubmit(): Promise<void> {
  const config = pageConfig.value?.deleteBinding
  if (!config || selectedRows.value.length === 0) return

  // 验证表单（如果有表单字段）
  if (deleteBindingFields.value.length > 0 && batchDeleteBindingFormRef.value) {
    const isValid = await batchDeleteBindingFormRef.value.validate()
    if (!isValid) return
  }

  // 获取表单数据（如果有表单）
  const formData = batchDeleteBindingFormRef.value?.getFormData() || {}
  const rows = [...selectedRows.value]

  batchDeleteBindingLoading.value = true
  batchDeleteBindingTotal.value = rows.length
  batchDeleteBindingCurrent.value = 0

  let successCount = 0
  let failCount = 0

  try {
    for (const row of rows) {
      batchDeleteBindingCurrent.value++
      batchDeleteBindingProgress.value = Math.round((batchDeleteBindingCurrent.value / batchDeleteBindingTotal.value) * 100)

      try {
        const targetData: Record<string, any> = { ...formData }

        // 1. 填充继承字段
        for (const mapping of config.inheritFields) {
          targetData[mapping.targetField] = row[mapping.sourceField]
        }

        // 2. 自动填充操作者信息
        if (config.autoFillOperator) {
          targetData._operatorName = authStore.user?.displayName || authStore.user?.username || ''
          targetData._operatorUsername = authStore.user?.username || ''
          targetData._deletedAt = new Date().toISOString()
        }

        // 3. 记录被删除记录信息
        targetData._sourceRecordId = row.id
        targetData._sourceCollection = collection.value

        // 4. 先创建目标记录
        await pageConfigStore.addPageData(`page-${config.targetCollection}`, targetData)

        // 5. 再删除原记录
        await pageConfigStore.deletePageData(pageId.value, row.id)

        successCount++
      } catch {
        failCount++
      }
    }

    batchDeleteBindingDialogVisible.value = false
    selectedRows.value = []
    dataTableRef.value?.clearSelection()

    if (failCount === 0) {
      ElMessage.success(`成功处理 ${successCount} 条记录`)
    } else {
      ElMessage.warning(`处理完成：成功 ${successCount} 条，失败 ${failCount} 条`)
    }

    await loadPageData()
  } catch (error) {
    ElMessage.error('批量操作失败')
  } finally {
    batchDeleteBindingLoading.value = false
  }
}

/**
 * 处理批量删除绑定表单提交事件
 */
async function handleBatchDeleteBindingFormSubmit(data: Record<string, any>): Promise<void> {
  batchDeleteBindingFormData.value = data
  await handleBatchDeleteBindingSubmit()
}

/**
 * 处理表单提交（从表单组件触发）
 */
async function handleSubmit(data: Record<string, any>): Promise<void> {
  await submitFormData(data)
}

/**
 * 处理表单提交按钮点击
 */
async function handleFormSubmit(): Promise<void> {
  // 验证表单
  const isValid = await dynamicFormRef.value?.validate()
  if (!isValid) return

  const formData = dynamicFormRef.value?.getFormData()
  if (formData) {
    await submitFormData(formData)
  }
}

/**
 * 提交表单数据
 */
async function submitFormData(data: Record<string, any>): Promise<void> {
  submitLoading.value = true
  try {
    const hasRelations = pageFields.value.some(f => f.controlType === 'relation')
    let savedRecordId = ''

    const doSave = async () => {
      // 分离关联字段和普通字段
      const regularData = pageConfigStore.stripRelationFields(pageId.value, data)

      if (isEditMode.value) {
        // 编辑模式：关联数据与主数据在同一事务中提交，保证原子性
        await pageConfigStore.updatePageData(pageId.value, currentRecord.value.id, regularData, data)
        savedRecordId = currentRecord.value.id
        ElMessage.success('更新成功')
      } else {
        // 新增模式：关联数据与主数据在同一事务中提交，保证原子性
        const created = await pageConfigStore.addPageData(pageId.value, regularData, undefined, data)
        savedRecordId = created.id
        ElMessage.success('新增成功')
      }
    }

    if (hasRelations) {
      const displayField = pageFields.value.find(f => !['autoTimestamp', 'autoSequence', 'relation'].includes(f.controlType))
      const name = (displayField ? data[displayField.fieldName] : '') || ''
      const actionLabel = isEditMode.value ? '修改' : '新增'
      await withBatch(`${actionLabel}${pageConfig.value?.name || '数据'}「${name}」`, doSave)
    } else {
      await doSave()
    }

    dialogVisible.value = false
    // 智能刷新：仅刷新受影响的单条记录，避免全量重新加载
    if (savedRecordId) {
      const refreshed = await pageConfigStore.refreshSingleRecord(pageId.value, savedRecordId)
      if (refreshed) {
        tableData.value = [...pageConfigStore.getCachedPageData(pageId.value)]
      } else {
        // 刷新单条失败，回退到全量加载
        await loadPageData()
      }
    } else {
      await loadPageData()
    }
  } catch (error: any) {
    const resp = error.response?.data
    if (resp?.code === 'VERSION_CONFLICT') {
      ElMessage.error('数据已被其他用户修改，请刷新后重试')
      dialogVisible.value = false
      await loadPageData()
    } else if (resp?.validationErrors?.length) {
      ElMessage.error(resp.validationErrors.join('；'))
      if (resp.validationWarnings?.length) {
        ElMessage.warning(resp.validationWarnings.join('；'))
      }
    } else if (resp?.error) {
      ElMessage.error(resp.error)
    } else {
      ElMessage.error(isEditMode.value ? '更新失败' : '新增失败')
    }
  } finally {
    submitLoading.value = false
  }
}

/**
 * 重新解析引用字段（quoteSelect / reference）
 */
async function handleReResolveReferences(): Promise<void> {
  try {
    const { updated, pending } = await pageConfigStore.reResolveReferences(pageId.value)
    if (updated > 0) await loadPageData()
    if (updated === 0 && pending === 0) {
      ElMessage.success('引用均已解析，无需更新')
    } else if (pending > 0) {
      ElMessage.warning(`已解析并更新 ${updated} 条；仍有 ${pending} 条未匹配（请确认被引用数据已导入）`)
    } else {
      ElMessage.success(`已重新解析，更新 ${updated} 条`)
    }
  } catch {
    ElMessage.error('重新解析失败')
  }
}

async function copyCollection() {
  try {
    await navigator.clipboard.writeText(collection.value)
    ElMessage.success(`已复制：${collection.value}`)
  } catch {
    ElMessage.info(collection.value)
  }
}

/**
 * 处理导出
 */
async function handleExport(): Promise<void> {
  const name = pageConfig.value?.name || '数据'
  // 全量拉取（绕过分页 1000 条限制），保留当前筛选条件
  let allData = tableData.value
  try {
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      keyword: searchKeyword.value || undefined,
      loadAll: true,
    })
    allData = result.data
  } catch {
    // 全量拉取失败时退化为当前页数据
  }
  if (allData.length === 0) {
    ElMessage.warning('暂无数据可导出')
    return
  }
  const relationDisplayMap = await pageConfigStore.fetchRelationDisplayMaps(pageId.value)
  const quoteDisplayMap = await pageConfigStore.fetchQuoteDisplayMaps(pageId.value)
  const mergedDisplayMap = { ...relationDisplayMap, ...quoteDisplayMap }
  exportToExcel(allData, effectiveFields.value, name, mergedDisplayMap)
  ElMessage.success('导出成功')
}

/**
 * 处理导出下拉命令（自定义脚本导出）
 */
async function handleExportCommand(command: string): Promise<void> {
  if (command === 'excel') {
    handleExport()
    return
  }
  // command is a script id
  if (tableData.value.length === 0) {
    ElMessage.warning('暂无数据可导出')
    return
  }
  const collection = pageId.value.replace('page-', '')
  if (!collection) {
    ElMessage.error('无法确定数据集合')
    return
  }
  try {
    await executeExportScript(command, collection)
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  }
}

/**
 * 处理行级导出
 */
async function handleRowExport(scriptId: string, row: DynamicRecord): Promise<void> {
  const collection = pageId.value.replace('page-', '')
  if (!collection) {
    ElMessage.error('无法确定数据集合')
    return
  }
  try {
    await executeExportScript(scriptId, collection, row.id)
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  }
}

/**
 * 处理导入下拉命令
 */
function handleImportCommand(command: string): void {
  if (command === 'template') {
    handleDownloadTemplate()
  } else if (command === 'import') {
    fileInputRef.value?.click()
  }
}

/**
 * 处理「更多」下拉菜单命令
 */
function handleMoreCommand(command: string): void {
  if (command === 'refresh') {
    handleRefresh()
    return
  }
  if (command === 'export') {
    handleExport()
  } else if (command.startsWith('script:')) {
    handleExportCommand(command.slice(7))
  } else if (command === 'import') {
    handleImportCommand('import')
  } else if (command === 'template') {
    handleImportCommand('template')
  } else if (command === 'version') {
    if (projectMenuId.value) {
      projectVersionManagerDefaultTab.value = 'versions'
      projectVersionManagerVisible.value = true
    } else {
      ElMessage.warning('该数据页不属于任何项目，无法使用版本管理')
    }
  } else if (command === 'dependency') {
    if (projectMenuId.value) {
      projectVersionManagerDefaultTab.value = 'dependencies'
      projectVersionManagerVisible.value = true
    } else {
      ElMessage.warning('该数据页不属于任何项目，无法使用依赖管理')
    }
  } else if (command === 'batchDelete') {
    handleBatchDeleteConfirm()
  } else if (command === 'reResolveRefs') {
    handleReResolveReferences()
  } else if (command === 'copyCollection') {
    copyCollection()
  }
}

/**
 * 下载导入模板
 */
function handleDownloadTemplate(): void {
  const name = pageConfig.value?.name || '数据'
  generateImportTemplate(pageFields.value, `${name}-导入模板`)
  ElMessage.success('模板已下载')
}

/**
 * 处理文件选择
 */
async function handleFileSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // 重置 input 以便下次选同一文件仍然触发
  input.value = ''

  try {
    const isJson = file.name.toLowerCase().endsWith('.json')
    const records = isJson
      ? await parseJsonImportFile(file, pageFields.value)
      : await parseImportFile(file, pageFields.value)
    if (records.length === 0) {
      ElMessage.warning('文件中没有可导入的数据')
      return
    }
    await doImport(records)
  } catch (error) {
    ElMessage.error('文件解析失败，请检查文件格式')
  }
}

/**
 * 执行批量导入
 */
async function doImport(records: Record<string, any>[]): Promise<void> {
  importResult.value = null
  importLoading.value = true
  importProgress.value = 0
  importCurrent.value = 0
  importTotal.value = records.length
  importDialogVisible.value = true

  const { success, failed, created, updated } = await importPageRecords({
    store: pageConfigStore,
    post,
    pageId: pageId.value,
    collection: collection.value,
    records,
    onProgress: (current, total) => {
      importCurrent.value = current
      importProgress.value = Math.round((current / total) * 100)
    },
  })

  importLoading.value = false
  importResult.value = { success, failed, created, updated }
  if (success > 0) await loadPageData()
}

/**
 * 处理刷新
 */
async function handleRefresh(): Promise<void> {
  await loadPageData()
  // 如果当前是 Excel 视图，同时刷新全量数据
  if (viewMode.value === 'excel') {
    await loadExcelData()
  }
  ElMessage.success('数据已刷新')
}

/**
 * 跳转导航 — 来源信息（用于返回导航栏）
 */
const jumpSource = computed(() => jumpStore.currentJumpSource)

/**
 * 关闭跳转来源提示栏
 */
function dismissJumpBar(): void {
  jumpStore.clearStack()
}

/**
 * 返回跳转来源页面
 */
function handleJumpBack(): void {
  const entry = jumpStore.popJump()
  if (!entry) return

  // 恢复源页面的筛选状态
  if (entry.filters) {
    jumpStore.setJump({
      targetCollection: '',
      targetRecordId: '',
      jumpType: 'relation',
      sourcePageId: pageId.value!,
      timestamp: Date.now(),
      _restore: entry.filters,
    } as any)
  }

  router.push({ path: entry.pagePath })
}

/**
 * 统一的记录跳转方法（通过 Store 传递意图，不使用 URL 参数）
 */
function navigateToRecord(targetCollection: string, targetRecordId: string, jumpType: string): void {
  const targetPageId = `page-${targetCollection}`
  const targetMenu = menuStore.menuList.find(m => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到目标数据的页面')
    return
  }

  // 获取当前页面名称用于返回导航栏显示
  const currentMenu = menuStore.menuList.find(m => m.pageId === pageId.value)
  const sourceEntry = {
    pagePath: route.path,
    pageName: currentMenu?.name || pageConfig.value?.name || '数据页面',
    pageId: pageId.value!,
    filters: {
      mongoQuery: activeMongoQuery.value ? JSON.parse(JSON.stringify(activeMongoQuery.value)) : null,
      keyword: searchKeyword.value,
      page: currentPage.value,
      pageSize: currentPageSize.value,
    },
  }

  // 设置跳转意图并保存来源到历史栈
  jumpStore.setJump(
    {
      targetCollection,
      targetRecordId,
      jumpType: jumpType as any,
      sourcePageId: pageId.value!,
    },
    sourceEntry
  )

  // 同页跳转：不做路由导航，直接定位记录
  if (targetPageId === pageId.value) {
    const intent = jumpStore.consumeJump()
    if (intent) {
      intelligentLocateRecord(intent.targetRecordId)
    }
    return
  }

  // 跨页跳转：仅 router.push，不携带 query 参数
  router.push({ path: targetMenu.path })
}

/**
 * 处理引用字段点击 — 跳转到被引用数据所在页面
 */
function handleReferenceClick(row: DynamicRecord, field: FieldConfig): void {
  const targetCollection = field.referenceConfig?.targetCollection
  if (!targetCollection) return

  const referencedId = row[field.fieldName] as string
  if (!referencedId) return

  navigateToRecord(targetCollection, referencedId, 'reference')
}

/**
 * 处理关联字段 Tag 点击 — 跳转到关联记录所在页面
 */
function handleRelationClick(relatedRecordId: string, field: FieldConfig): void {
  const targetCollection = field.relationConfig?.targetCollection
  if (!targetCollection) return

  navigateToRecord(targetCollection, relatedRecordId, 'relation')
}

/**
 * 处理引用选择字段 Tag 点击 — 跳转到引用记录所在页面
 */
function handleQuoteClick(quotedRecordId: string, field: FieldConfig): void {
  const targetCollection = field.quoteConfig?.targetCollection
  if (!targetCollection) return

  navigateToRecord(targetCollection, quotedRecordId, 'quote')
}

/**
 * 打开关系图谱对话框
 */
function handleShowRelationGraph(row: DynamicRecord): void {
  graphRecordId.value = row.id
  graphDialogVisible.value = true
}

/**
 * 处理图谱节点双击跳转
 */
function handleGraphNodeNavigate(targetCollection: string, targetRecordId: string): void {
  navigateToRecord(targetCollection, targetRecordId, 'graph')
}

// ==================== 列视图方法 ====================

/**
 * 处理视图选择
 */
function handleViewSelect(viewId: number | null): void {
  columnViewStore.selectView(pageId.value, viewId)
}

/**
 * 打开视图管理弹窗
 */
function handleOpenManage(): void {
  viewManageDialogRef.value?.open()
}

/**
 * 处理列配置编辑（从视图管理弹窗触发）
 */
function handleEditColumns(view: any): void {
  editingViewId.value = view.id
  columnConfigDialogRef.value?.open(
    view.columns,
    view.sortConfig || [],
    view.groupConfig?.field || null
  )
}

/**
 * 处理列配置保存
 *
 * 保存到「正在编辑的视图」(editingViewId)，而不是当前应用的视图——二者可能不同
 * （在视图管理弹窗里可以编辑任意视图，包括尚未应用的视图）。
 */
async function handleColumnConfigSave(
  columns: any[],
  sortConfig: any[],
  groupField: string | null
): Promise<void> {
  const viewId = editingViewId.value
  if (viewId == null) return

  try {
    const groupConfig = groupField ? { field: groupField } : null
    await columnViewStore.updateView(pageId.value, viewId, {
      columns,
      sortConfig,
      groupConfig
    })
    ElMessage.success('列配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

/**
 * 加载列视图
 */
async function loadColumnViews(): Promise<void> {
  if (!pageId.value) return
  try {
    await columnViewStore.loadViews(pageId.value)
  } catch {
    // 加载失败不影响页面功能
  }
}

/**
 * 高亮定位指定记录
 */
async function highlightRecord(recordId: string): Promise<void> {
  await nextTick()

  const row = tableData.value.find(r => r.id === recordId)
  if (!row) return

  const elTable = dataTableRef.value?.tableRef
  if (elTable) {
    elTable.setCurrentRow(row)
  }

  await nextTick()
  const tableEl = dataTableRef.value?.$el as HTMLElement | undefined
  if (tableEl) {
    const currentRow = tableEl.querySelector('.el-table__body tr.current-row') as HTMLElement
    if (currentRow) {
      currentRow.scrollIntoView({ behavior: 'smooth', block: 'center' })
      currentRow.classList.add('highlight-flash')
      setTimeout(() => currentRow.classList.remove('highlight-flash'), 2000)

      ElMessage.success({
        message: '已定位到目标记录',
        duration: 2000
      })
    }
  }
}

// ==================== 监听 ====================

/** 跳转加载标志位 — 防止 onActivated 与 pageId watcher 并行加载 */
let jumpLoadInProgress = false

/**
 * 监听页面ID变化，重新加载数据
 * 首次访问时检查是否有待消费的跳转意图
 */
watch(
  () => pageId.value,
  async (newPageId) => {
    if (!newPageId) return

    // 检查是否有待消费的跳转（跨页跳转到达时）
    const pendingJump = jumpStore.consumeJump()

    if (pendingJump && (pendingJump as any)._restore) {
      // 返回导航：恢复筛选状态
      jumpLoadInProgress = true
      const filters = (pendingJump as any)._restore
      activeMongoQuery.value = filters.mongoQuery
      searchKeyword.value = filters.keyword
      currentPage.value = filters.page
      currentPageSize.value = filters.pageSize
      await loadPageData()
      jumpLoadInProgress = false
      return
    }

    if (pendingJump) {
      // 跳转到达：检查是否需要切换分支
      jumpLoadInProgress = true
      currentPage.value = 1

      // 检查跳转意图是否携带分支ID且不是main
      if (pendingJump.branchId && pendingJump.branchId !== 'main' && projectMenuId.value) {
        // 自动切换到跳转携带的分支
        try {
          const result = await switchProjectBranch(pendingJump.branchId, projectMenuId.value)
          await loadCurrentBranch()
          ElMessage.success(`已切换到分支：${result.branchName}`)
        } catch (error) {
          console.error('分支切换失败:', error)
          ElMessage.warning('跳转携带的分支不存在或已失效，保持在当前分支')
        }
      }

      await loadPageDataWithLocate(pendingJump.targetRecordId)
      jumpLoadInProgress = false
      return
    }

    // 普通页面切换
    currentPage.value = 1
    loadPageData()
  },
  { immediate: true }
)

/**
 * 使用 locateId 加载数据并高亮目标记录
 */
async function loadPageDataWithLocate(targetId: string): Promise<void> {
  if (!pageId.value) return

  tableLoading.value = true
  try {
    // 按需加载 page_configs
    if (!pageConfigStore.pageConfigs.length) {
      await pageConfigStore.fetchPageConfigs()
    }

    // 使用 locateId 请求后端定位目标记录所在页
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      keyword: searchKeyword.value || undefined,
      pageSize: currentPageSize.value,
      locateId: targetId,
    })

    // 情况 A：在当前筛选条件下找到了
    if (result.locatedPage != null) {
      tableData.value = result.data
      totalCount.value = result.total
      currentPage.value = result.locatedPage
      await highlightRecord(targetId)
      return
    }

    // 情况 B：记录不匹配当前筛选条件，清除筛选重试
    if (result.locateFilterMiss) {
      activeMongoQuery.value = null
      searchKeyword.value = ''

      const retryResult = await pageConfigStore.fetchPageData(pageId.value, {
        pageSize: currentPageSize.value,
        locateId: targetId,
      })

      if (retryResult.locatedPage != null) {
        tableData.value = retryResult.data
        totalCount.value = retryResult.total
        currentPage.value = retryResult.locatedPage
        await highlightRecord(targetId)
        ElMessage.info('已临时清除筛选条件以定位记录')
        return
      }
    }

    // 情况 C：记录不存在
    ElMessage.warning('未找到目标记录，可能已被删除')
    // 回退到正常加载
    await loadPageData()
  } catch (error) {
    console.error('定位记录失败:', error)
    ElMessage.error('定位记录失败')
    await loadPageData()
  } finally {
    tableLoading.value = false
  }
}

/**
 * 智能定位目标记录（同页跳转使用）
 */
async function intelligentLocateRecord(targetId: string): Promise<void> {
  if (!pageId.value) return

  // 先检查当前页数据
  if (tableData.value.find(r => r.id === targetId)) {
    await highlightRecord(targetId)
    return
  }

  // 当前页没有，通过 locateId 重新加载
  await loadPageDataWithLocate(targetId)
}


/**
 * 搜索关键字变化时触发后端搜索（防抖 300ms）
 * 关键字搜索由后端处理，支持普通字段和关联字段
 */
watch(searchKeyword, () => {
  // 清除之前的定时器
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
  }

  // 防抖处理
  searchDebounceTimer = setTimeout(async () => {
    currentPage.value = 1
    await loadPageData()
    // 如果当前是 Excel 视图，同时刷新全量数据
    if (viewMode.value === 'excel') {
      await loadExcelData()
    }
  }, 300)
})

/**
 * 数据变化时清除 DataTable 单元格缓存
 * 确保新数据能正确显示
 */
watch(tableData, () => {
  dataTableRef.value?.clearCellValueCache?.()
}, { deep: false })

/**
 * 页面ID变化时清除筛选条件并加载列视图
 */
watch(pageId, (newPageId) => {
  columnFilters.value = {}
  searchMode.value = 'keyword'
  activeMongoQuery.value = null
  mongoQueryText.value = ''
  aiSearchText.value = ''
  aiSearchLoading.value = false
  aiGeneratedFilter.value = null
  searchKeyword.value = ''
  // Set default view mode from config
  viewMode.value = pageConfig.value?.viewConfig?.defaultView || 'table'
  // 清除并重新加载列视图
  columnViewStore.clearState()
  if (newPageId) {
    loadColumnViews()
  }
})

/**
 * 视图模式切换时延迟渲染 Excel 视图
 * 避免大量数据同时渲染导致的卡顿
 */
watch(viewMode, (newMode, oldMode) => {
  // 离开 Excel 视图时，保存快照
  if (oldMode === 'excel' && newMode !== 'excel') {
    excelViewRef.value?.saveSnapshot()
  }

  if (newMode === 'excel') {
    // 首次切换到 Excel 视图时初始化组件
    if (!excelInitialized.value) {
      excelInitialized.value = true
    }
    // 显示加载占位，延迟显示实际组件
    excelReady.value = false
    // 加载全量数据
    loadExcelData().then(() => {
      // 使用 setTimeout 让浏览器先完成当前渲染帧
      setTimeout(() => {
        excelReady.value = true
      }, 50)
    })
  } else if (newMode === 'calendar') {
    // 切换到日历视图时，等待 DOM 渲染完成后刷新尺寸
    // 避免 v-show 隐藏时初始化导致的布局压缩问题
    nextTick(() => {
      setTimeout(() => {
        calendarViewRef.value?.getApi()?.updateSize()
      }, 100)
    })
  } else {
    // 切换到其他视图时立即隐藏 Excel 视图
    excelReady.value = false
  }
})

// ==================== 生命周期 ====================

/** 标记是否需要立即刷新数据（用于跨页面操作后强制刷新） */
const needsRefresh = ref(false)

/**
 * 暴露刷新方法供外部调用
 * 用于双向关联同步后强制刷新
 */
function markNeedsRefresh(): void {
  needsRefresh.value = true
}

defineExpose({ markNeedsRefresh })

onActivated(async () => {
  // 重新加载当前页面的列视图，防止 keep-alive 缓存导致跨页面视图配置串扰
  if (pageId.value) {
    await loadColumnViews()
  }

  // 检查是否有待消费的跳转（从缓存页面返回或跳转到缓存页面时）
  const pendingJump = jumpStore.consumeJump()
  if (pendingJump) {
    if ((pendingJump as any)._restore) {
      // 返回导航：恢复筛选状态
      const filters = (pendingJump as any)._restore
      activeMongoQuery.value = filters.mongoQuery
      searchKeyword.value = filters.keyword
      currentPage.value = filters.page
      currentPageSize.value = filters.pageSize
      await loadPageData()
    } else {
      // 跳转到达：检查是否需要切换分支
      // 检查跳转意图是否携带分支ID且不是main
      if (pendingJump.branchId && pendingJump.branchId !== 'main' && projectMenuId.value) {
        // 自动切换到跳转携带的分支
        try {
          const result = await switchProjectBranch(pendingJump.branchId, projectMenuId.value)
          await loadCurrentBranch()
          ElMessage.success(`已切换到分支：${result.branchName}`)
        } catch (error) {
          console.error('分支切换失败:', error)
          ElMessage.warning('跳转携带的分支不存在或已失效，保持在当前分支')
        }
      }
      // 定位目标记录
      await loadPageDataWithLocate(pendingJump.targetRecordId)
    }
    // 跳转完成，跳过正常缓存逻辑
  } else if (!jumpLoadInProgress) {
    // 正常 keep-alive 激活逻辑（排除 pageId watcher 正在跳转加载的情况）
    const cachedData = pageConfigStore.getCachedPageData(pageId.value)
    if (needsRefresh.value) {
      needsRefresh.value = false
      await loadPageData()
    } else if (cachedData.length === 0) {
      await loadPageData()
    }
  }

  // 加载导出脚本列表（用于绑定展示）
  try {
    allExportScripts.value = await getExportScripts()
  } catch {
    // 非管理员可能无权访问，忽略错误
  }
})
</script>

<style scoped lang="scss">
.dynamic-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 12px;
  margin-bottom: 10px;

  .page-title {
    flex: 1 1 auto;
    min-width: 0;

    .title-row {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 30px;

      h2 {
        margin: 0;
        font-size: 17px;
        font-weight: 650;
        color: var(--el-text-color-primary);
        letter-spacing: -0.01em;
      }

      .page-subtitle {
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }

      .el-tag {
        margin-left: 4px;
      }

      .branch-switch-link {
        color: var(--el-color-primary);
        font-size: 13px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;

        &:hover {
          color: var(--el-color-primary-light-3);
        }
      }
    }
  }

  .page-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;

    .search-zone {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .header-search {
      width: 200px;
    }

    .header-search--wide {
      width: 320px;
    }

    .header-search--mono :deep(.el-input__inner) {
      font-family: monospace;
    }

    .search-mode-select {
      width: 104px;
    }

    .search-chip {
      flex-shrink: 0;
    }

    .query-help-icon {
      cursor: pointer;
      color: var(--el-text-color-placeholder);

      &:hover {
        color: var(--el-color-primary);
      }
    }

    .actions-divider {
      width: 1px;
      height: 18px;
      background: var(--el-border-color);
      margin: 0 2px;
    }

    .view-toggle {
      margin: 0 2px;
    }
  }
}

:deep(.dropdown-group-label.is-disabled) {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  cursor: default;
  opacity: 1;
  padding-top: 4px;
  padding-bottom: 2px;
  font-weight: 600;
}

.import-progress {
  text-align: center;
  padding: 20px 0;

  p {
    margin-top: 12px;
    color: #606266;
  }
}

.import-result {
  padding: 10px 0;
}

.jump-source-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background-color: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 4px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--el-text-color-regular);

  .jump-source-icon {
    color: var(--el-color-primary);
    font-size: 16px;
  }

  strong {
    color: var(--el-color-primary);
  }
}

.query-help {
  font-size: 13px;
  line-height: 1.6;

  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 8px;

    td {
      padding: 4px 8px;
      border-bottom: 1px solid #f0f0f0;
      vertical-align: top;
    }

    td:first-child {
      font-family: monospace;
      font-size: 12px;
      white-space: nowrap;
      color: #606266;
    }

    td:last-child {
      color: #909399;
    }
  }
}

.table-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 0;
    overflow: hidden;
    box-sizing: border-box;
  }

  :deep(.highlight-flash) {
    animation: row-flash 0.6s ease-in-out 3;
  }
}

.kanban-card {
  overflow: auto;

  :deep(.el-card__body) {
    overflow: auto;
  }
}

.calendar-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 16px;
    overflow: auto;
    box-sizing: border-box;
  }
}

.gantt-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 0;
    overflow: hidden;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }
}

.excel-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;

  :deep(.el-card__body) {
    height: 100%;
    padding: 0;
    overflow: hidden;
    box-sizing: border-box;
  }
}

.excel-loading-placeholder {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--el-text-color-secondary);
  background: var(--el-bg-color);
  z-index: 10;

  .el-icon {
    font-size: 48px;
    color: #409eff;
  }

  span {
    font-size: 14px;
  }
}

@keyframes row-flash {
  0%, 100% { background-color: transparent; }
  50% { background-color: #ecf5ff; }
}

// 针对深色模式的覆盖
html.dark {
  .dynamic-page {
    :deep(.highlight-flash) {
      animation: dark-row-flash 0.6s ease-in-out 3;
    }
  }
}

@keyframes dark-row-flash {
  0%, 100% { background-color: transparent; }
  50% { background-color: rgba(255, 237, 153, 0.3); }
  }

// 定义高亮颜色变量
// 亮色模式
:root {
  --dynamic-page-highlight-color: #ecf5ff;
}
// 深色模式
html.dark {
  --dynamic-page-highlight-color: rgba(255, 237, 153, 0.3); // 浅黄色，在深色模式下更明显
}

// 针对深色模式的覆盖
html.dark .dynamic-page :deep(.highlight-flash) {
  animation: dark-row-flash 0.6s ease-in-out 3;
}

@keyframes dark-row-flash {
  0%, 100% { background-color: transparent; }
  50% { background-color: rgba(255, 237, 153, 0.3); } /* 深色模式的高亮颜色 */
}

.relation-tags {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.relation-tag-link {
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #409eff;
    border-color: #409eff;
  }
}

.reference-link {
  color: #409eff;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}

.view-textarea {
  white-space: pre-wrap;
  word-break: break-all;
}

.view-richtext {
  max-height: 300px;
  overflow-y: auto;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.view-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.view-image-item {
  width: 80px;
  height: 80px;
  border-radius: 4px;
}

.deleted-record-info {
  margin-bottom: 16px;
}

.batch-progress {
  text-align: center;
  padding: 20px 0;

  p {
    margin-top: 12px;
    color: #909399;
  }
}
</style>

<style>
/* 分支切换下拉菜单滚动限制 */
.branch-dropdown-menu {
  max-height: 300px;
  overflow-y: auto;
}
</style>
