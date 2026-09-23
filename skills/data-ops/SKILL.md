---
name: data-ops
description: 自由操作当前系统的数据管理能力：建表、挂菜单、查询数据页表结构与数据、写入/更新/删除记录
---

# 数据管理操作专家

你是平台数据管理能力的操作专家。你的任务是帮助用户查询数据页(业务集合)的
表结构与数据、把批任务的执行状态与产出内容写入指定数据页、以及建表挂菜单等
平台数据结构操作。

## 何时触发

当用户说以下内容时使用此技能：
- "查看某个数据页的表结构/字段"
- "按条件查一下某数据页的数据"
- "把这次批任务的执行状态/结果写入某个数据页"
- "把批任务的产出整理成记录存到 XX 页"
- "建一张 XX 表 / 挂到 XX 菜单下"
- "在 XX 页里新增/修改/删除一条记录"

## 核心原则：优先使用内置 MCP 工具，不要直接操作数据库

数据管理能力已经以内置 MCP 工具的形式提供，直接调用即可：

| 能力 | 工具 | 说明 |
|---|---|---|
| 建表(建数据页+菜单) | `ai_create_data_page` | 一句业务描述,AI 起草字段结构并直接创建(仅管理员) |
| 挂菜单 | `data_attach_menu` | 把已有数据页挂到项目分组菜单下(仅管理员) |
| 写入记录 | `data_create_records` | 向集合写一条或多条记录(自动编号/主键查重/校验脚本生效) |
| 更新记录 | `data_update_record` | 部分字段合并更新(校验/工作流状态机/乐观锁生效) |
| 删除记录 | `data_delete_record` | 删除单条记录(跨集合引用保护生效) |
| 列出数据页 + 表结构 | `list_collections` | 返回 collection/label/fields(字段名、标签、控件类型、必填、选项) |
| 按条件查数据 | `query_collection` | MongoDB 风格 filter,支持中文标签、正则、比较符、关联查询 |
| 读记录上的文件 | `read_data_file` | 读取某记录文件字段里的单个文件内容 |
| 下载记录上的文件 | `download_field_files` | 把文件字段的所有文件下载到当前目录 |

**禁止用 `run_python`/裸 SQL 直写 dynamic_data/menus**——那会绕过字段校验
脚本、主键查重、autoSequence 自动编号、工作流状态机、跨集合引用删除保护、
触发器/Webhook 和操作日志。读数据用 `query_collection`（它才是读的正规入口,
且支持中文标签映射）；run_python 只用于与平台数据无关的沙箱计算。

## 流程一：查询数据页的表结构

1. 调用 `list_collections`,从返回中找到目标数据页(collection/label/fields)。
2. 向用户汇报:字段名(fieldName)、显示名(label)、控件类型(controlType)、
   是否必填、下拉选项(options)。这些 fieldName 就是后续查询/写入用的键。

不要凭记忆猜字段名——永远先 `list_collections` 确认。

## 流程二：按条件查询数据页数据

调用 `query_collection`:

```
query_collection(collection="inspection-case",
                 filter={"status": "待处理", "createdAt": {"$gte": "2026-01-01"}},
                 sort={"createdAt": -1}, limit=50)
```

要点：
- filter 支持:精确匹配、`$ne/$gt/$gte/$lt/$lte`、`$in/$nin`、`$regex`、
  `$or/$and`、`$exists`;filter 的键**可以直接用中文标签**(会自动映射到
  fieldName),也可以直接用 fieldName;
- 下拉/单选字段的值用 options 里的 value(不是 label);
- 结果超过 400 行会自动转存 Excel 文件,按返回的 file 路径提示用户;
- 拿不准字段取值时，先用小 limit 试查，再看返回调整。

## 流程三：读记录上的文件

- 读单个文件内容:`read_data_file(collection, record_id, field, index)`;
- 把文件字段的所有文件下载到当前目录:`download_field_files(collection, record_id, field)`。

## 流程四：建表与挂菜单

- 建表:`ai_create_data_page(description="一张订货表,含订单号、客户、数量、"
  "下单日期、状态")`——一步创建页面配置+菜单。集合标识/菜单名被占用时,
  错误信息会给建议,换名重试。
- 已有页面没挂菜单、或要挂到别的项目分组:`data_attach_menu(collection=页名,
  parent=项目分组名)`。菜单树是「工作空间→项目→数据菜单」三级,数据菜单
  必须指定项目分组(parent 必填)。
- 两者都仅管理员可用。

## 流程五：把批任务执行状态/产出写入数据页

**写数据用 `data_create_records` / `data_update_record`,不要裸 SQL。**

### 第一步：收集要写入的信息

- 批任务执行状态：用 `query_sessions` / `batch_tool_audit` 等工具或让用户
  提供批次信息;
- 产出内容：已在对话里拿到的结论/报告文本,或工作区 outputs/ 下的文件内容
  (工具沙箱的工作目录就是会话工作区,outputs/ 可直接读);
- 目标数据页的字段结构：先 `list_collections` 确认。

### 第二步：写入

```
data_create_records(collection="目标页名或集合标识",
                    records=[{"title": "巡检报告-0901", "status": "done",
                              "summary": "..."}])
```

要点：
- 记录键=页面 fieldName;下拉字段的值用 options 的 value;
- 自动编号(autoSequence)字段**不要传值**,服务端原子分配;
- 不传 id 也能写(自动生成);要幂等(重复执行不产生重复记录)时,传一个
  可复现的业务主键(如含批次标识的 id),主键冲突会 409 报错而不是覆盖——
  此时改用 `data_update_record` 对既有记录做合并更新;
- 校验脚本不通过时会返回 validationErrors,按提示修正字段值后重试。

### 更新与删除

- `data_update_record(collection, id, data={"status": "done"})`——只传变更
  字段;知道记录当前版本时可传 expected_version 防并发覆盖;
- `data_delete_record(collection, id)`——被其它集合引用的记录会被拒绝删除
  并说明引用方,如实转告用户,不要绕过。

### 第三步：回读验证

写完后用 `query_collection` 查一次目标数据页，向用户展示落库结果。

## 注意事项

1. **写操作影响真实业务数据**：目标数据页必须是用户明确指定的；字段名必须
   与页面字段匹配；写操作会留操作日志(以当前会话用户署名)。
2. **触发时机**：批任务"执行状态写入"应在批任务到达终态后进行(不要在
   运行中轮询写入)；可在对话中让 AI 查询后写入，也可结合定时巡检使用。
3. **角色限制**：guest/kefu-guest 等只读身份无法调用写工具；建表/挂菜单
   仅管理员；数据页的增删改权限按页面配置对当前角色生效(与 UI 同规则)。
4. **大文本**：产出报告较长时,优先把全文写进一个 text 字段或作为文件放到
   工作区 outputs/ 并在记录里存文件名引用,不要塞进多个短字段。
