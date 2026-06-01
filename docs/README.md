# Check-Manage 文档中心

本目录包含Check-Manage系统的完整技术文档，按类型分类组织。

## 目录结构

### 📘 [user-guide/](./user-guide/) - 用户使用手册
面向最终用户的功能使用文档，包含：
- 核心功能使用指南
- 查询与分析功能说明
- 系统集成与扩展指南
- 部署与运维手册

### 📕 [design/](./design/) - 设计文档
面向开发人员的技术设计文档，包含：
- 系统架构设计（[系统设计文档.md](./design/系统设计文档.md)）
- 项目管理功能设计、数据并发控制设计
- 跨项目依赖设计（含 `assets/` 下的动画/示意图）
- AI Chat 功能说明与方案设计
- 巡检用例开发 Skill 设计
- 优势分析报告

### 📗 [issues/](./issues/) - 问题修复、测试与优化
历史问题修复记录、测试用例和系统优化文档，包含：
- 版本管理相关问题、跨集合版本问题
- Bug 修复验证报告、项目 Bug 分析报告
- 数据一致性修复方案、关联跳转优化
- 测试用例文档、手工测试完成报告

### 📙 [superpowers/](./superpowers/) - 开发流程文档
使用 Superpowers 流程产出的规范文档：
- `plans/` - 实现计划
- `specs/` - 设计规范

## 快速导航

### 新用户入门
1. 阅读 [user-guide/使用说明.md](./user-guide/使用说明.md)
2. 了解数据关系：[user-guide/数据关系类型使用说明.md](./user-guide/数据关系类型使用说明.md)
3. 学习查询语法：[user-guide/数据查询语法手册.md](./user-guide/数据查询语法手册.md)
4. 全量功能说明：[user-guide/全量功能说明文档.md](./user-guide/全量功能说明文档.md)

### 开发人员
1. 了解系统架构：[design/系统设计文档.md](./design/系统设计文档.md)
2. 阅读并发控制：[design/数据并发控制设计文档.md](./design/数据并发控制设计文档.md)
3. 跨项目依赖设计：[design/跨项目依赖设计文档.md](./design/跨项目依赖设计文档.md)
4. AI Chat 设计：[design/ai-chat-功能说明与方案设计.md](./design/ai-chat-功能说明与方案设计.md)
5. 查看 OpenAPI：[user-guide/OpenAPI接口文档.md](./user-guide/OpenAPI接口文档.md)

### 问题排查
查看 [issues/](./issues/) 目录下的历史问题/测试文档，了解常见问题和解决方案。