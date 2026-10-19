# 状态徽标字段（statusBadge） 设计文档

日期：2026-07-11

## 背景与目标

某数据页需要一个"处理状态"字段：录入一条记录会触发 webhook，第三方系统异步处理，处理过程中会分阶段（如：待处理 → 处理中 → 已完成/失败）通过现有 Open API 回写这个字段的值。业务人员需要在列表和详情里**直观看到当前处理阶段**（图标/颜色，"处理中"要有动画感，而不是干巴巴的文字），并且在数据还没到终态时，页面要**自动定时刷新**直到看到最终结果。

这个字段：
- 值仍是普通字符串（跟 `select` 一样存），第三方系统按现有 Open API（`PUT /api/v1/collections/<collection>/<id>`，支持部分字段更新 + `_version` 乐观锁，API Key 鉴权）直接回写，**不新增任何专门的状态回写接口**。
- 在表单里**永远只读**——业务人员不能手动改这个字段，只能通过第三方回写变化。
- 支持配置**超时兜底**：如果这个字段超过指定时长没有变化，后端定时任务自动把它写成配置好的"失败"值并持久化（列表/导出都能看到真实的失败状态），不是前端临时显示。

## 架构方案

新增一个独立的 `controlType: 'statusBadge'`（不复用/不改动现有 `select`），因为它的语义（只读展示、图标/颜色/动画、驱动轮询）跟"可编辑单选"完全不同。项目里"新增字段控件类型"已有标准流程（`CLAUDE.md` "Adding a New Field Control Type" 一节），本设计照此流程走：

1. `src/types/field.ts` 的 `ControlType` 联合类型（12-31 行）新增 `'statusBadge'`；`CONTROL_TYPE_OPTIONS`（38-58 行）同步加一项，供字段配置界面选择这个控件类型。
2. `FieldConfig` 新增可选属性 `statusBadgeConfig`：
   ```ts
   export interface StatusBadgeOption {
     value: string
     label: string
     icon: string        // Element Plus 图标名，复用已有的 IconPicker.vue 选择器
     color?: string       // 十六进制色值，不填则用 Element Plus 默认灰
     animated?: boolean   // true 时图标持续旋转（表示"进行中"）
     terminal?: boolean   // true 表示这是终态——到达后停止轮询
   }
   export interface StatusBadgeConfig {
     options: StatusBadgeOption[]
     pollIntervalSec?: number  // 轮询间隔，默认 5
     timeoutSec?: number       // 超时阈值（秒），不填则不启用超时兜底
     timeoutValue?: string     // 超时后写入的选项值，必须是 options 里某个 value（通常指向 terminal:true 的"失败"选项）
   }
   ```
   （字段名沿用现有 `disabled` 属性把这个字段在表单里锁成只读，不新增专门的只读开关。）
3. 新建只读展示组件 `src/components/dynamic-form/controls/StatusBadge.vue`：图标 + 文字（颜色取自 `color`），`animated` 为真时加旋转动画 class。不接受用户交互（没有 click/change，纯展示）。在 `controls/index.ts` 的 `controlComponentMap`（35-55 行）里注册 `statusBadge: StatusBadge`；`getControlDefaultValue`（74-96 行）为它返回空字符串（新建记录时由字段的 `defaultValue` 决定初始阶段，如 `pending`）。
4. `src/components/common/DataTable.vue`：
   - 表格列模板（59-93 行那一段 `v-if`/`v-else-if` 链）新增一支 `v-else-if="field.controlType === 'statusBadge'"`，渲染小图标+文字（复用 Step 3 的展示逻辑或抽成共享的展示片段）。
   - `formatCellValue`（505-583 行，`default:` 之前）新增分支，返回对应选项的 `label`（供无法用模板富渲染的场景——如导出——退化成纯文本）。
5. 详情弹窗：项目里"查看记录"是**独立的只读详情弹窗**（`DynamicPage.vue` 的 `viewDialogVisible` + `<el-descriptions>`，不是 `DynamicForm.vue` 编辑表单——两者是分开的），已经有一条按 `controlType` 分支的 `v-if`/`v-else-if` 渲染链（`relation`/`quoteSelect`/`reference`/`select`等）加一个 `formatViewValue(field)` 纯文本兜底函数。新增一支 `v-else-if="field.controlType === 'statusBadge'"`，渲染方式与表格列一致（图标更大一点）；`formatViewValue` 也加一个 `statusBadge` 分支返回选项 `label`。
6. 后端：`server/routes/dynamic.py` 当前**没有任何按 `controlType` 做的校验**（未知/新 `controlType` 会被直接存储、不报错），新增这个 `controlType` 本身不需要后端校验改动。但为了支持"超时兜底"，需要一处小的后端改动——见下一节。

## 轮询刷新

参照项目里已有的轮询模式（`src/stores/aiChatBatches.ts` 的 `listTimer`/`startListPolling`/`stopListPolling` + 调用方 `onUnmounted` 清理），不用原生裸 `setInterval` 散落在组件里：

只做**一套页面级轮询**（不额外做详情弹窗轮询）：`DynamicPage.vue` 里"查看记录"弹窗（`viewRecord`）的数据本来就是 `handleView(row)` 时从当前表格行数据直接拷贝的一份快照（不是单独发请求查询），所以列表刷新的同时顺带把打开中的详情弹窗也同步了。

- 页面配置里只要存在至少一个 `controlType === 'statusBadge'` 的字段，当前页数据拉取完成后检查：是否有任意一行该字段的值命中一个 `terminal !== true` 的选项。命中则按该字段 `statusBadgeConfig.pollIntervalSec`（默认 5s；如页面有多个 statusBadge 字段，取其中最小的间隔）启动一个定时器，重新调用现有的 `pageConfigStore.fetchPageData(pageId, {...当前分页/筛选条件})` 刷新整页。
- 每次刷新后：① 重新判断是否还有非终态行，全部终态则自动停止定时器；② 如果"查看记录"弹窗当前是打开的（`viewDialogVisible === true`）且 `viewRecord.id` 在刷新后的数据里能找到，把 `viewRecord` 更新成刷新后的那一行（找不到就不动，防御性处理，避免记录被删/翻页导致的异常）。
- 组件卸载（`onUnmounted`）时停止定时器，避免离开页面后仍在后台轮询。

## 超时兜底

判定"多久没刷新"用的是这个状态字段**自己**上次变化的时间，不是记录整行的 `updated_at`（避免业务人员改了记录其他字段导致超时计时被误延长）。做法：

1. **变化时间戳**：每当某条记录的 `statusBadge` 字段的值真正发生变化（新旧值不同）时，在同一次写入里把变化时刻塞进 `data` 的一个隐藏子字段 `_statusBadge_<fieldName>_changedAt`（ISO 时间字符串），沿用项目里已有的 `_rel_*`/`_quote_*`/`_ref_*` 隐藏计算字段命名习惯。记录**创建**时如果该字段有 `defaultValue`，也同样初始化一次这个时间戳（否则超时判断无基准点）。
   - `server/routes/dynamic.py`：创建路径 `create_item`（`:518`），在现有的按 `controlType` 注入派生值的逻辑（`autoSequence` 默认值注入，`:561-567`）同一处追加：`statusBadge` 字段有值时写入 `_statusBadge_<fieldName>_changedAt`。更新路径 `update_item`（`:637`），在 `merged_data = {**old_data, **data}`（`:698`）之后、UPDATE SQL（`:770`）之前，参照已有的按字段比对新旧值的模式（`:741-767` 的 workflow 状态对比循环），对每个 `statusBadge` 字段比较 `old_data.get(fieldName)` 与 `merged_data[fieldName]`，不同则更新时间戳。
   - `server/routes/open_api.py`：`update_collection_item`（`:424`），`fields` 已经通过 `get_page_fields(cur, collection)`（`:105`）取到（含 `controlType`），在 `merged.update(new_data)`（`:466-469`）之后、UPDATE SQL（`:486`）之前做同样的新旧值比较+时间戳写入。这是第三方系统实际回写状态会走的路径，必须覆盖。
2. **定时任务**：新增 `server/utils/status_badge_timeout_scheduler.py`，仿照 `ai_scan_scheduler.py` 的模式（`APScheduler` 1 分钟 tick，`start_scan_scheduler(app)` 同款注册方式，`app.py` 里同一个 `WERKZEUG_RUN_MAIN` 守卫下新增一行启动调用）：每次 tick 扫描所有配置了 `statusBadge` 且设了 `timeoutSec` 的字段（遍历 `page_configs.fields`），对其 collection 下 `data->>fieldName` 命中非 `terminal` 选项、且 `_statusBadge_<fieldName>_changedAt` 早于 `now - timeoutSec` 的记录，直接把该字段写成 `timeoutValue`、时间戳刷新为当前时刻。
3. **不触发 webhook**：这次写入是后端直接 `UPDATE dynamic_data SET data = ...`，不经过 `dynamic.py`/`open_api.py` 的路由逻辑，因此天然不会调用 `webhook_engine.fire_webhooks(...)`（该函数只在这两处路由里被显式调用，`:851`/`dynamic.py` 的 after-webhook，`open_api.py` 的更新路径当前根本不调用它）——避免第三方系统收到自己造成的"超时"这个奇怪的回环通知。
4. **前端零改动**：超时之后数据库里的值变了，跟第三方正常回写没有区别，第「轮询刷新」节里已经设计好的机制会自然感知到并停止轮询，不需要任何额外的前端超时逻辑。

## 图标选择

字段配置界面里，`statusBadgeConfig.options` 每一项的 `icon` 用已有的 `src/components/common/IconPicker.vue`（`v-model` 选 Element Plus 图标名，带搜索），与首页快速录入区块"图标"字段用的是同一个组件，保持体验一致。

## 测试

- 后端 pytest：`create_item`/`update_item`（`dynamic.py`）与 `update_collection_item`（`open_api.py`）在 `statusBadge` 字段值变化时正确写入 `_statusBadge_<fieldName>_changedAt`，值未变化时不重复刷新时间戳；定时任务扫描逻辑（命中超时的记录被写成 `timeoutValue` 且时间戳刷新、未超时/已终态的记录不受影响、写入不调用 `fire_webhooks`）。
- 前端 vitest：`StatusBadge.vue` 按不同 `value`/`animated` 渲染正确图标/颜色/动画 class 的组件测试；列表页轮询 store action 的单测（命中非终态时启动定时器、全终态时停止、组件卸载时清理）。
- Playwright（遵循 CLAUDE.md 强制要求）：配置一个带 `statusBadge` 字段（含 `timeoutSec`）的测试数据页，创建一条记录（初始"待处理"图标），用 Open API（或直接改库）模拟第三方回写到"处理中"再到"已完成"，验证列表/详情在轮询间隔内自动刷新展示，到终态后确认轮询确实停止（不再发多余请求）；另建一条记录卡在"处理中"不回写，验证定时任务超时后自动写成失败态、前端轮询感知到并停止。

## 范围之外（明确不做）

- 不做数值百分比进度条（本设计只支持离散阶段+图标）。
- 不新增专门的状态回写接口，复用现有 Open API。
- 不支持业务人员手动修改这个字段（表单里固定只读）。
- 不引入 SSE，只用定时轮询。
