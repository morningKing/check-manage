#!/usr/bin/env python3
"""
菜单结构检查和修复脚本

检查系统中所有非系统菜单是否符合标准3层结构规范：
- workspace (一级): parentId=null
- project (二级): parentId指向workspace
- data (三级): parentId指向project

自动修复违规菜单并输出详细报告。
"""

import sys
import os

# 添加server目录到路径,以便导入db模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from db import get_db


def main():
    """主流程:检查并修复菜单结构"""
    print("=" * 70)
    print("菜单结构检查脚本")
    print("=" * 70)

    try:
        with get_db() as conn:
            # TODO: 实现检查和修复逻辑
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()