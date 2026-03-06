# 数据查询语法手册

本文档是数据查询控制台的完整语法参考。每个语法点均附带可直接粘贴运行的案例。

> 入口：系统配置 → 数据工具 → 数据查询

---

## 一、查询语句结构

一条完整查询由以下字段组成：

```json
{
  "collection": "inspection-case",
  "query": {},
  "lookup": [],
  "select": [],
  "sort": {},
  "skip": 0,
  "limit": 200
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `collection` | string | 是 | 目标集合标识 |
| `query` | object | 否 | 查询条件，留空 `{}` 表示查询全部 |
| `lookup` | array | 否 | 连表查询，可同时连多张表 |
| `select` | array | 否 | 字段投影，只返回指定列 |
| `sort` | object | 否 | 排序，`1` 升序、`-1` 降序，支持多字段 |
| `skip` | number | 否 | 跳过条数，默认 `0` |
| `limit` | number | 否 | 返回上限，默认 `200`，最大 `2000` |

**说明：** 所有字段名均可使用**中文标签**或**英文字段名**，系统自动映射。例如 `"用例ID"` 会被自动转换为内部字段名 `"caseid"`。

---

## 二、精确匹配

直接写 `"字段": "值"` 即为精确等于。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例ID": "IC-001"
  }
}
```

等价写法（显式 `$eq`）：

```json
{
  "collection": "inspection-case",
  "query": {
    "用例ID": { "$eq": "IC-001" }
  }
}
```

---

## 三、比较操作符

### 3.1 不等于 `$ne`

```json
{
  "collection": "inspection-case",
  "query": {
    "用例类型": { "$ne": "废弃" }
  },
  "limit": 200
}
```

查出类型**不为**"废弃"的所有用例。

### 3.2 大于 `$gt` / 大于等于 `$gte`

```json
{
  "collection": "inspection-case",
  "query": {
    "优先级": { "$gt": 2 }
  }
}
```

对数字字段做数值比较。`$gte` 含等号。

### 3.3 小于 `$lt` / 小于等于 `$lte`

```json
{
  "collection": "inspection-case",
  "query": {
    "优先级": { "$lte": 3 }
  }
}
```

### 3.4 范围组合

同一字段上可组合多个操作符，表示区间：

```json
{
  "collection": "inspection-case",
  "query": {
    "优先级": { "$gte": 1, "$lte": 3 }
  },
  "limit": 200
}
```

查出优先级在 1～3 之间的记录。

### 3.5 null 值比较

```json
{
  "collection": "inspection-case",
  "query": {
    "用例描述": null
  }
}
```

查出描述为空的记录。反过来用 `$ne` 可查非空：

```json
{
  "collection": "inspection-case",
  "query": {
    "用例描述": { "$ne": null }
  }
}
```

---

## 四、字符串操作符

### 4.1 正则匹配 `$regex`

不区分大小写的正则匹配（底层为 PostgreSQL `~*`）。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例名称": { "$regex": "登录.*验证" }
  },
  "sort": { "用例ID": 1 },
  "limit": 100
}
```

查出名称匹配正则 `登录.*验证` 的用例（如"登录功能验证""登录密码验证"等）。

### 4.2 模糊匹配 `$like`

自动在前后加 `%`，等价于 SQL 的 `ILIKE '%value%'`。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例名称": { "$like": "登录" }
  },
  "limit": 100
}
```

查出名称中包含"登录"二字的用例。比 `$regex` 更简单，适合不需要正则的场景。

---

## 五、数组操作符

### 5.1 包含 `$in`

值在给定列表中的记录。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例类型": {
      "$in": ["功能巡检", "安全巡检", "性能巡检"]
    }
  },
  "sort": { "用例类型": 1, "用例ID": 1 },
  "limit": 500
}
```

查出类型为三者之一的用例，按类型、用例ID双重排序。

### 5.2 不包含 `$nin`

值**不在**给定列表中的记录（含字段为空的记录）。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例类型": {
      "$nin": ["废弃", "暂停"]
    }
  },
  "limit": 200
}
```

---

## 六、元素操作符

### 6.1 字段存在 `$exists`

```json
{
  "collection": "inspection-case",
  "query": {
    "用例描述": { "$exists": true },
    "修改日期": { "$exists": true }
  },
  "select": ["用例ID", "用例名称", "用例描述"],
  "limit": 200
}
```

查出同时填写了描述和修改日期的用例。`false` 表示字段不存在或为空。

### 6.2 数组长度 `$size`

```json
{
  "collection": "inspection-case",
  "query": {
    "巡检项目": { "$size": 3 }
  },
  "limit": 100
}
```

查出巡检项目恰好有 3 项的用例。

---

## 七、逻辑操作符

### 7.1 逻辑或 `$or`

任一条件满足即匹配。

```json
{
  "collection": "inspection-case",
  "query": {
    "$or": [
      { "用例类型": "功能巡检" },
      { "优先级": "高" }
    ]
  },
  "sort": { "用例ID": 1 },
  "limit": 200
}
```

查出类型为功能巡检、**或者**优先级为高的用例。

### 7.2 逻辑与 `$and`

所有条件都必须满足。

```json
{
  "collection": "inspection-case",
  "query": {
    "$and": [
      { "用例名称": { "$like": "登录" } },
      { "用例类型": "功能巡检" },
      { "优先级": "高" }
    ]
  },
  "limit": 200
}
```

> 提示：同一层级写多个字段本身就是隐式 AND，`$and` 主要用于同一字段出现多次的场景。

### 7.3 逻辑非 `$not`

对条件取反。

```json
{
  "collection": "inspection-case",
  "query": {
    "用例名称": {
      "$not": { "$regex": "废弃|暂停" }
    }
  },
  "limit": 200
}
```

查出名称中**不包含**"废弃"或"暂停"的用例。

### 7.4 全部不满足 `$nor`

```json
{
  "collection": "inspection-case",
  "query": {
    "$nor": [
      { "用例类型": "废弃" },
      { "优先级": "低" }
    ]
  },
  "limit": 200
}
```

查出类型不为"废弃"**且**优先级不为"低"的用例。

### 7.5 组合嵌套

逻辑操作符可以自由嵌套。

```json
{
  "collection": "inspection-case",
  "query": {
    "$or": [
      {
        "$and": [
          { "用例类型": "功能巡检" },
          { "优先级": "高" }
        ]
      },
      {
        "$and": [
          { "用例类型": "安全巡检" },
          { "用例名称": { "$like": "权限" } }
        ]
      }
    ]
  },
  "limit": 200
}
```

查出 (功能巡检且高优先级) 或 (安全巡检且名称含权限) 的用例。

---

## 八、连表查询 `lookup`

通过 `lookup` 将关联集合的数据展开到查询结果中。

### 语法

```json
"lookup": [
  {
    "from": "目标集合标识",
    "localField": "本集合的关联字段",
    "as": "结果列名"
  }
]
```

系统根据字段的 `controlType` 自动识别关联方式：

| 关联类型 | 字段类型 | 连表方式 | 返回格式 |
|----------|----------|----------|----------|
| M:N 多对多 | `relation` | 通过 `data_relations` 中间表 | 数组 |
| 父子引用 | `reference` | 本地字段存父记录ID | 单个对象 |
| 引用选择 | `quoteSelect` | 本地字段存ID数组 | 数组 |

### 8.1 M:N 关联 — 巡检用例 + 巡检模板

```json
{
  "collection": "inspection-case",
  "query": {
    "用例ID": { "$regex": "IC" }
  },
  "lookup": [
    {
      "from": "c38df47f",
      "localField": "模板id集合",
      "as": "巡检模板"
    }
  ],
  "sort": { "用例ID": 1 },
  "limit": 100
}
```

`模板id集合` 是 relation 类型字段，结果中「巡检模板」列为数组，每项是一条模板记录。

### 8.2 多表同时连查

```json
{
  "collection": "inspection-case",
  "query": {},
  "lookup": [
    {
      "from": "c38df47f",
      "localField": "模板id集合",
      "as": "巡检模板"
    },
    {
      "from": "f6b8f361",
      "localField": "风险模板ID集合",
      "as": "风险模板"
    }
  ],
  "select": ["用例ID", "用例名称", "用例类型"],
  "limit": 50
}
```

同时展开巡检模板和风险模板。`select` 只返回 3 个业务字段 + 2 个 lookup 列。

### 8.3 父子引用 — PPT配置 → 巡检用例

```json
{
  "collection": "288c3ebc",
  "query": {
    "操作类型": { "$regex": "检查" }
  },
  "lookup": [
    {
      "from": "inspection-case",
      "localField": "用例ID",
      "as": "所属用例"
    }
  ],
  "limit": 100
}
```

`用例ID` 是 reference 类型字段，结果中「所属用例」列为单个对象（父记录）。

### 8.4 引用选择 — PPT配置 → 巡检模板

```json
{
  "collection": "288c3ebc",
  "query": {},
  "lookup": [
    {
      "from": "c38df47f",
      "localField": "模板ID",
      "as": "引用模板"
    }
  ],
  "limit": 100
}
```

`模板ID` 是 quoteSelect 类型字段，结果中「引用模板」列为数组。

### 8.5 混合连表 — 同时连 reference + quoteSelect

```json
{
  "collection": "288c3ebc",
  "query": {},
  "lookup": [
    {
      "from": "inspection-case",
      "localField": "用例ID",
      "as": "所属用例"
    },
    {
      "from": "c38df47f",
      "localField": "模板ID",
      "as": "引用模板"
    }
  ],
  "limit": 100
}
```

---

## 九、投影 `select`

控制返回哪些列。不填则返回全部。

```json
{
  "collection": "inspection-case",
  "query": {},
  "select": ["用例ID", "用例名称", "用例类型", "优先级"],
  "limit": 200
}
```

> lookup 的 `as` 列会自动保留，无需在 `select` 中重复写。

---

## 十、排序 `sort`

`1` 升序，`-1` 降序。支持多字段：

```json
{
  "collection": "inspection-case",
  "query": {},
  "sort": {
    "用例类型": 1,
    "用例ID": -1
  },
  "limit": 200
}
```

先按类型升序，同类型内按用例ID降序。

---

## 十一、分页

通过 `skip` + `limit` 实现分页，在查询控制台界面中翻页会自动设置。

```json
{
  "collection": "inspection-case",
  "query": {},
  "sort": { "用例ID": 1 },
  "skip": 100,
  "limit": 50
}
```

跳过前 100 条，返回第 101～150 条。

---

## 十二、综合案例

### 案例 A：条件筛选 + 连表 + 投影 + 排序

查出名称含"登录"的功能巡检用例，关联展开巡检模板，只返回关键列，按用例ID排序：

```json
{
  "collection": "inspection-case",
  "query": {
    "用例名称": { "$like": "登录" },
    "用例类型": "功能巡检"
  },
  "lookup": [
    {
      "from": "c38df47f",
      "localField": "模板id集合",
      "as": "巡检模板"
    }
  ],
  "select": ["用例ID", "用例名称", "优先级"],
  "sort": { "用例ID": 1 },
  "limit": 200
}
```

### 案例 B：$or + $in + 连表

查出 (高优先级或安全巡检类型) 且巡检项目包含指定值的用例，同时关联风险模板：

```json
{
  "collection": "inspection-case",
  "query": {
    "$or": [
      { "优先级": "高" },
      { "用例类型": "安全巡检" }
    ],
    "巡检项目": { "$exists": true }
  },
  "lookup": [
    {
      "from": "f6b8f361",
      "localField": "风险模板ID集合",
      "as": "风险模板"
    }
  ],
  "sort": { "优先级": -1 },
  "limit": 200
}
```

### 案例 C：从子表反查 + 条件过滤

查出所有 irisk 数据，展开其所属的巡检用例，并过滤版本号：

```json
{
  "collection": "a23a385c",
  "query": {
    "版本": { "$ne": "" }
  },
  "lookup": [
    {
      "from": "inspection-case",
      "localField": "用例ID",
      "as": "所属用例"
    }
  ],
  "limit": 200
}
```

---

## 附录：集合关系图

```
巡检用例 (inspection-case)
  ├─ [relation M:N]     模板id集合      ──> 巡检模板 (c38df47f)
  └─ [relation M:N]     风险模板ID集合  ──> 风险模板 (f6b8f361)

PPT配置 (288c3ebc)
  ├─ [reference 父子]   用例ID          ──> 巡检用例 (inspection-case)
  └─ [quoteSelect]      模板ID          ──> 巡检模板 (c38df47f)

irisk数据 (a23a385c)
  └─ [reference 父子]   用例ID          ──> 巡检用例 (inspection-case)

巡检模板 (c38df47f)
  └─ [relation M:N]     用例集合        ──> 巡检用例 (inspection-case)

风险模板 (f6b8f361)
  └─ [relation M:N]     用例ID集合      ──> 巡检用例 (inspection-case)
```

---

## 附录：操作符速查表

| 类别 | 操作符 | 值类型 | 说明 |
|------|--------|--------|------|
| 比较 | `$eq` | any | 等于（可省略） |
| 比较 | `$ne` | any | 不等于 |
| 比较 | `$gt` | number/string | 大于 |
| 比较 | `$gte` | number/string | 大于等于 |
| 比较 | `$lt` | number/string | 小于 |
| 比较 | `$lte` | number/string | 小于等于 |
| 数组 | `$in` | array | 值在列表中 |
| 数组 | `$nin` | array | 值不在列表中 |
| 字符串 | `$regex` | string | 正则匹配（不区分大小写） |
| 字符串 | `$like` | string | 模糊匹配（自动加 %） |
| 元素 | `$exists` | boolean | 字段是否存在 |
| 元素 | `$size` | integer | 数组长度等于 |
| 逻辑 | `$and` | array | 所有条件都满足 |
| 逻辑 | `$or` | array | 任一条件满足 |
| 逻辑 | `$not` | object | 条件取反 |
| 逻辑 | `$nor` | array | 所有条件都不满足 |

---

## 附录：快捷操作

| 操作 | 说明 |
|------|------|
| `Ctrl + Enter` | 执行查询 |
| `Ctrl + Space` | 触发自动补全 |
| 点击右侧字段名 | 插入到查询语句 |
| 点击语法参考条目 | 复制片段到剪贴板 |
| 「格式化」按钮 | 格式化 JSON |
| 「导出 Excel」按钮 | 导出当前结果 |
