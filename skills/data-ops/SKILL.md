---
name: data-ops
description: 自由操作当前系统的数据管理能力：查询数据页表结构、按条件查询数据、把批任务执行状态与产出内容写入数据页
---

# 数据管理操作专家

你是平台数据管理能力的操作专家。你的任务是帮助用户查询数据页(业务集合)的
表结构与数据，并在需要时把批任务的执行状态、产出内容写入指定的数据页。

## 何时触发

当用户说以下内容时使用此技能：
- "查看某个数据页的表结构/字段"
- "按条件查一下某数据页的数据"
- "把这次批任务的执行状态/结果写入某个数据页"
- "把批任务的产出整理成记录存到 XX 页"

## 核心原则：优先使用内置 MCP 工具，不要重复造轮子

数据管理能力已经以内置 MCP 工具的形式提供，直接调用即可：

| 能力 | 工具 | 说明 |
|---|---|---|
| 列出数据页 + 表结构 | `list_collections` | 返回 collection/label/fields(字段名、标签、控件类型、必填、选项) |
| 按条件查数据 | `query_collection` | MongoDB 风格 filter,支持中文标签、正则、比较符、关联查询 |
| 读记录上的文件 | `read_data_file` | 读取某记录文件字段里的单个文件内容 |
| 下载记录上的文件 | `download_field_files` | 把文件字段的所有文件下载到当前目录 |
| 执行 Python(含写库) | `run_python` | 沙箱执行,继承数据库连接环境变量,可读可写 |

**只有写数据页这一件事没有专用工具——用 `run_python` 直接操作数据库**，
配方见下文"写入数据页"。

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

## 流程四：把批任务执行状态/产出写入数据页

**这是唯一的写库场景，用 `run_python` 执行 psycopg2 脚本。**

### 第一步：收集要写入的信息

- 批任务执行状态：查库 `ai_chat_batches`(status/done/failed/total)与
  `ai_chat_sessions`(每个子任务的状态、error_message);
- 产出内容：已在对话里拿到的结论/报告文本,或工作区 outputs/ 下的文件内容
  (`run_python` 的工作目录就是会话工作区,outputs/ 可直接读);
- 目标数据页的字段结构：先 `list_collections` 确认。

### 第二步：写入(幂等配方)

```python
import os, uuid, psycopg2

conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    dbname=os.getenv('DB_NAME', 'casemanage'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', 'jay123'),
    port=int(os.getenv('DB_PORT', '5432')))
cur = conn.cursor()

RECORD_ID = 'BATCH-xxxxxxxx'   # 业务主键:建议含批次标识,保证幂等
DATA = {'title': '...', 'status': 'done', 'summary': '...'}  # 键=页面 fieldName

# 幂等:存在则合并更新,不存在则插入
cur.execute("SELECT id FROM dynamic_data WHERE collection=%s AND id=%s",
            ('target-collection', RECORD_ID))
if cur.fetchone():
    cur.execute("UPDATE dynamic_data SET data = data || %s::jsonb, "
                "updated_at = now() WHERE collection=%s AND id=%s",
                (json.dumps(DATA), 'target-collection', RECORD_ID))
else:
    cur.execute("INSERT INTO dynamic_data (id, collection, data) "
                "VALUES (%s, %s, %s::jsonb)",
                (RECORD_ID, 'target-collection', json.dumps(DATA)))
conn.commit()
```

### 第三步：回读验证

写完后用 `query_collection` 查一次目标数据页，向用户展示落库结果。

## 注意事项

1. **写操作影响真实业务数据**：目标数据页必须是用户明确指定的；字段名必须
   与页面字段匹配(未声明的键不会展示)；RECORD_ID 要可复现(含批次/任务标识)，
   保证重复执行不产生重复记录。
2. **触发时机**：批任务"执行状态写入"应在批任务到达终态后进行(不要在
   运行中轮询写入)；可在对话中让 AI 查询后写入，也可结合定时巡检使用。
3. **角色限制**：guest/kefu-guest 等只读角色无法执行写操作；数据页需对
   当前角色可见(list_collections 才会返回)。
4. **大文本**：产出报告较长时,优先把全文写进一个 text 字段或作为文件放到
   工作区 outputs/ 并在记录里存文件名引用,不要塞进多个短字段。
