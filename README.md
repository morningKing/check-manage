# 巡检用例管理系统

基于动态配置的巡检用例管理平台，支持灵活的菜单、页面和字段配置，实现无需编码即可扩展业务数据页面。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3 + TypeScript + Pinia + Element Plus + Vite |
| 后端 | Flask + psycopg2 |
| 数据库 | PostgreSQL |
| 认证 | JWT (Bearer Token) |

## 功能概览

- **动态数据页面** — 根据页面配置自动渲染表单和表格，支持 13 种字段控件
- **菜单管理** — 支持 3 级嵌套菜单，可配置图标、路由、关联页面和角色可见性
- **页面配置** — 可视化字段编辑器，定义数据页的字段结构和校验规则
- **用户管理** — admin / developer / guest 三角色体系，基于角色的权限控制
- **数据关联** — 多对多双向关联（relation）和一对多引用依赖（reference）
- **Excel 导入导出** — 支持模板下载、批量导入、格式化导出
- **操作日志** — 全操作审计，支持按时间/类型/操作人筛选和导出
- **系统备份** — 手动/定时备份，ZIP 导出，一键还原

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
pip install -r server/requirements.txt
```

### 配置数据库

编辑 `server/config.py`，设置 PostgreSQL 连接信息：

```python
DB_CONFIG = {
    'host': 'localhost',
    'dbname': 'casemanage',
    'user': 'postgres',
    'password': 'your_password',
    'port': 5432,
}
```

### 初始化数据库

```bash
python server/init_db.py
```

将自动创建表结构、插入种子数据和默认管理员账号（admin / admin123）。

### 启动服务

```bash
# 后端（端口 3001）
python server/app.py

# 前端（端口 5173，自动代理 /api → 后端）
npm run dev
```

访问 `http://localhost:5173`，使用 admin / admin123 登录。

## 项目结构

```
check-manage/
├── server/                  # Flask 后端
│   ├── app.py              # 应用入口 + 蓝图注册
│   ├── auth.py             # JWT 认证装饰器
│   ├── config.py           # 数据库和应用配置
│   ├── db.py               # 连接池
│   ├── init_db.py          # DDL + 数据迁移 + 种子数据
│   ├── seed_data.py        # 初始菜单/页面配置/示例数据
│   ├── routes/             # API 路由
│   │   ├── auth.py         # 登录/修改密码
│   │   ├── users.py        # 用户管理
│   │   ├── menus.py        # 菜单 CRUD
│   │   ├── page_configs.py # 页面配置 CRUD
│   │   ├── dynamic.py      # 动态数据 CRUD（通用）
│   │   ├── relations.py    # 数据关联管理
│   │   ├── operation_logs.py # 操作日志
│   │   └── backups.py      # 系统备份
│   └── utils/
│       ├── operation_log.py # 审计日志辅助函数
│       └── backup.py       # 备份/还原/调度器
├── src/                     # Vue 3 前端
│   ├── api/                # API 接口封装
│   ├── components/         # 通用组件 + 动态表单 + 布局
│   ├── router/             # 路由配置 + 动态路由
│   ├── stores/             # Pinia 状态管理
│   ├── types/              # TypeScript 类型定义
│   ├── utils/              # 工具函数（请求/Excel/校验）
│   └── views/              # 页面组件
│       ├── admin/          # 管理页面
│       ├── dynamic/        # 动态数据页
│       ├── home/           # 首页
│       └── login/          # 登录页
├── docs/                    # 文档
└── package.json
```

## 文档

- [系统设计文档](docs/系统设计文档.md) — 架构设计、数据模型、数据依赖关系
- [使用说明](docs/使用说明.md) — 功能介绍和操作指南
- [数据关联使用说明](docs/数据关联使用说明.md) — 关联和引用字段详解

## License

MIT
