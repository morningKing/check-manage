# 巡检用例管理系统

配置驱动的动态数据管理平台。通过菜单配置和页面字段定义，无需编码即可创建业务数据页面，支持数据关联、导入导出、校验脚本、ETL 数据管道和 Open API 等功能。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3 + TypeScript + Element Plus + Pinia |
| 后端 | Python Flask + psycopg2 |
| 数据库 | PostgreSQL (JSONB 灵活存储) |
| 代码编辑 | CodeMirror 6 |
| 认证 | JWT (Bearer Token) |
| 构建工具 | Vite |

## 功能概览

- **动态数据页面** — 通过配置定义字段结构，自动生成表单和表格，支持 15 种控件类型
- **菜单管理** — 可视化树形菜单编辑，支持 3 级嵌套，基于角色的可见性控制
- **数据关联** — 多对多双向关联（relation）和一对多引用（reference），自动双向同步
- **自动字段** — 自动时间戳（autoTimestamp）和自增序列（autoSequence），新增/编辑时自动填充
- **导入导出** — Excel 模板导入、Excel 导出、自定义 Python 脚本导出（支持 json/csv/xml/txt/html）
- **数据校验** — Python 校验脚本绑定到页面，新增和编辑时自动执行，支持关联数据校验
- **ETL 数据管道** — 可视化步骤编排，支持 HTTP 抽取、脚本转换、字段映射、条件过滤、写入集合
- **Open API** — API Key 认证，外部系统按集合访问数据
- **用户权限** — 三级角色体系（admin / developer / guest），基于角色的路由和菜单控制
- **操作审计** — 全量操作日志，支持批次聚合、筛选、导出
- **系统备份** — 手动/定时备份，支持下载、还原、跨环境迁移
- **数据对比** — 当前数据与历史备份、不同备份之间的逐字段差异比较，支持导出对比报告

## 快速开始

### 环境要求

- Node.js >= 18
- Python >= 3.9
- PostgreSQL >= 13

### 安装依赖

```bash
# 前端
npm install

# 后端
pip install flask flask-cors psycopg2-binary
```

### 配置数据库

编辑 `server/config.py`，设置 PostgreSQL 连接信息：

```python
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'dbname': 'casemanage',
    'user': 'postgres',
    'password': 'your_password',
}
```

### 初始化数据库

```bash
cd server
python init_db.py
```

将自动创建所有数据表（13 张业务表）并初始化系统菜单和默认管理员账号。

### 启动服务

```bash
# 方式一：同时启动前后端
npm run dev:all

# 方式二：分别启动
npm run server    # 后端（端口 3001）
npm run dev       # 前端（端口 5173，自动代理 /api → 后端）
```

访问 `http://localhost:5173`，使用 admin / admin123 登录。

## 项目结构

```
check-manage/
├── server/                    # Flask 后端
│   ├── app.py                 # 应用入口
│   ├── config.py              # 配置文件
│   ├── init_db.py             # 数据库初始化
│   ├── routes/                # 路由模块
│   │   ├── auth.py            # 认证
│   │   ├── menus.py           # 菜单管理
│   │   ├── page_configs.py    # 页面配置
│   │   ├── dynamic.py         # 动态数据 CRUD
│   │   ├── relations.py       # 数据关联
│   │   ├── users.py           # 用户管理
│   │   ├── export_scripts.py  # 导出脚本
│   │   ├── validation_scripts.py # 校验脚本
│   │   ├── etl_tasks.py       # ETL 管理
│   │   ├── api_keys.py        # Open API
│   │   ├── operation_logs.py  # 操作日志
│   │   └── backups.py         # 系统备份 + 数据对比
│   └── utils/                 # 工具模块
│       ├── db.py              # 数据库连接池
│       ├── auth.py            # JWT 认证
│       ├── script_runner.py   # 脚本沙箱执行器
│       ├── etl_engine.py      # ETL 执行引擎
│       ├── operation_log.py   # 操作日志记录
│       └── backup.py          # 备份/还原逻辑
├── src/                       # Vue 前端
│   ├── api/                   # API 请求层
│   ├── components/            # 公共组件
│   │   ├── common/            # 通用组件（DataTable, BackupDiffDialog 等）
│   │   ├── dynamic-form/      # 动态表单组件（含 15 种控件）
│   │   └── layout/            # 布局组件
│   ├── router/                # 路由配置
│   ├── stores/                # Pinia 状态管理
│   ├── types/                 # TypeScript 类型定义
│   ├── utils/                 # 工具函数
│   └── views/                 # 页面组件
│       ├── admin/             # 系统管理页面
│       ├── dynamic/           # 动态数据页面
│       ├── home/              # 首页
│       └── login/             # 登录页
├── docs/                      # 文档
│   ├── 系统设计文档.md         # 架构与技术设计
│   ├── 使用说明.md             # 用户操作手册
│   └── 数据关联使用说明.md     # 关联功能详解
└── package.json
```

## 数据库表

| 表名 | 说明 |
|------|------|
| menus | 菜单树结构 |
| page_configs | 页面配置（含 JSONB 字段定义） |
| dynamic_data | 所有业务数据（JSONB 灵活存储） |
| data_relations | 多对多关联关系 |
| users | 用户账号 |
| export_scripts | 导出脚本 |
| validation_scripts | 校验脚本 |
| etl_tasks | ETL 任务定义 |
| etl_logs | ETL 执行日志 |
| api_keys | Open API 密钥 |
| operation_logs | 操作审计日志 |
| backups | 备份记录 |
| backup_settings | 定时备份配置 |

## 文档

- [使用说明](docs/使用说明.md) — 用户操作手册，覆盖所有功能的使用方法
- [系统设计文档](docs/系统设计文档.md) — 架构设计、数据库结构、API 接口、安全模型
- [数据关联使用说明](docs/数据关联使用说明.md) — 多对多关联功能的配置和使用详解

## 构建部署

```bash
# 构建前端
npm run build

# 产出目录：dist/
# 配合 Nginx 等 Web 服务器部署静态文件
# 后端通过 gunicorn 或直接 python app.py 运行
```

## License

MIT
