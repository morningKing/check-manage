# 脚本上传使用手册

## 概述

本系统支持通过上传 .py 文件将本地编写的导出脚本或校验脚本注入系统。开发人员可以在本地 IDE（如 VSCode、PyCharm）中编写和调试脚本,完成后上传到系统。

## 上传流程

### 1. 本地编写脚本

在本地 IDE 创建 `.py` 文件,编写导出或校验逻辑。

### 2. 上传文件

1. 登录系统(需要 admin 权限)
2. 进入「导出脚本管理」或「校验脚本管理」页面
3. 点击「上传脚本」按钮
4. 选择本地 `.py` 文件

### 3. 填写信息

上传后:
- 脚本内容自动填充到编辑器
- 文件名(不含 .py 后缀)作为默认脚本名称
- 可继续在线编辑调整代码

### 4. 保存脚本

填写脚本名称和描述后,点击「保存」完成创建。

### 5. 测试验证

使用「测试」功能验证脚本正确性,确保能正常执行。

## 文件要求

| 要求 | 说明 |
|------|------|
| 文件类型 | `.py`(Python 脚本文件) |
| 文件编码 | UTF-8 |
| 文件大小 | 不超过 100KB |

### 编码注意事项

如果上传后脚本内容出现乱码:
1. 确保本地文件保存为 UTF-8 编码
2. 在在线编辑器中手动修正乱码部分
3. 重新保存

## 本地开发建议

### VSCode 配置

1. 安装 Python 插件(Microsoft 官方)
2. 设置默认编码为 UTF-8:
   ```json
   {
     "files.encoding": "utf8"
   }
   ```
3. 使用代码格式化工具(如 Black)保持代码整洁

### PyCharm 配置

1. 设置文件编码:Settings → Editor → File Encodings → UTF-8
2. 启用 Python 代码补全和语法检查

### 调试技巧

由于系统沙箱环境限制(禁用 `import`、`open` 等函数),本地调试时建议:

1. 模拟入参变量:
   ```python
   # 本地调试时模拟数据
   data = [{'id': '1', 'name': '测试'}]
   fields = [{'fieldName': 'name', 'label': '名称'}]
   page_name = '测试页面'

   # 实际脚本代码
   result = json.dumps(data, ensure_ascii=False)
   print(result)
   ```

2. 仅使用预注入模块:
   - json, re, math, collections, datetime, timedelta
   - 导出脚本额外支持:csv, io, ET (xml.etree.ElementTree), minidom
   - 可选:pd (pandas), np (numpy)

## 脚本模板参考

### 导出脚本模板(JSON 格式)

```python
# ============================================
# 导出脚本 — JSON 格式
# ============================================
# 入参变量(系统自动注入):
#   data       : list[dict]  — 数据记录
#   fields     : list[dict]  — 字段配置
#   page_name  : str         — 页面名称
#   references : dict         — 被引用记录查找表 {集合:{id:记录}}(含跨项目依赖)
#
# 输出变量:
#   result     : str | bytes — 导出内容(必须)
#   filename   : str         — 文件名(可选)
#   content_type : str       — MIME类型(可选)
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)
```

> **`references` —— 跨页/跨项目引用查找表**：当数据页含 `reference` / `quoteSelect` / `relation` 字段时,系统会在跑脚本前自动解析这些外键(包括按**跨项目依赖**声明的分支/版本补取被引用页的记录),注入 `references = {目标集合: {id: 记录}}`。脚本里 `references.get('目标集合', {}).get(外键ID)` 即可拿到被引用记录,无需自己再查库;值为 `None` 表示该 ID 已尝试但缺失(悬挂引用)。此前仅菜单级脚本可用,现整页/单行级(含 JSON 导出)也已注入。
>
> ```python
> # 例:订单导出时把「产品」外键替换成产品名称
> out = []
> for r in data:
>     prod = references.get('products', {}).get(r.get('productId'))
>     out.append({**r, 'productName': prod['name'] if prod else None})
> result = json.dumps(out, ensure_ascii=False)
> ```

### 导出脚本模板(CSV 格式)

```python
# ============================================
# 导出脚本 — CSV 格式
# ============================================

output = io.StringIO()
writer = csv.writer(output)

# 写入表头
headers = [f['label'] for f in fields]
writer.writerow(headers)

# 写入数据行
for row in data:
    writer.writerow([str(row.get(f['fieldName'], '')) for f in fields])

result = output.getvalue()
```

### 校验脚本模板

```python
# ============================================
# 校验脚本
# ============================================
# 入参变量(系统自动注入):
#   record     : dict         — 当前提交的数据
#   action     : str          — 'create' 或 'update'
#   old_data   : dict | None  — 修改前的旧数据
#   fields     : list[dict]   — 字段配置
#   collection : str          — 当前集合名
#
# 校验输出:
#   add_error(msg)   — 添加错误(阻止保存)
#   add_warning(msg) — 添加警告(不阻止)
#
# 查询函数:
#   query(collection)               — 查询全部记录
#   query_one(collection, id)       — 按 ID 查询
#   find_by(collection, field, val) — 按字段值查找
#   get_relations(collection, id)   — 查询现有关联
#
# 关联函数:
#   set_relations(field, target_col, target_field, ids)
# ============================================

# 示例:必填校验
if not record.get('name'):
    add_error('名称不能为空')

# 示例:唯一性校验(仅新增时)
if action == 'create':
    existing = find_by(collection, 'name', record.get('name'))
    if existing:
        add_error('名称已存在')
```

## 常见问题

### Q1: 上传后脚本乱码怎么办?

确保本地文件使用 UTF-8 编码保存。VSCode 可通过右下角编码指示器切换。

### Q2: 保存时报"import statements are not allowed"?

系统沙箱禁止使用 `import` 语句。请使用预注入的模块(json, csv, io, re 等),它们无需导入即可直接使用。

### Q3: 脚本执行超时怎么办?

脚本执行超时为 60 秒(菜单级导出为 300 秒)。如果超时:
1. 检查是否有死循环
2. 优化数据处理逻辑
3. 减少数据量(分批处理)

### Q4: 上传的脚本可以继续编辑吗?

可以。上传后内容填充到编辑器,可继续在线编辑调整代码,然后保存。

### Q5: 如何验证上传的脚本是否正确?

使用页面内的「测试」按钮:
- 导出脚本:使用示例数据运行,预览输出结果
- 校验脚本:使用示例数据运行,检查错误/警告输出

## 相关文档

- 导出脚本详细说明:导出脚本管理页面 → 使用说明 Tab
- 校验脚本详细说明:校验脚本管理页面 → 使用说明 Tab
- 系统架构:CLAUDE.md