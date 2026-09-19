# -*- coding: utf-8 -*-
"""One-off: rebrand 白泽 → BKB across all surfaces."""
import os

BRAND_FULL = 'BKB · 数据智能平台'
BRAND_SHORT = 'BKB'

# ── 1. index.html ────────────────────────────────────────────────────────
p = 'index.html'
s = open(p, encoding='utf-8').read()
s = s.replace('白泽 · 数据智能平台', BRAND_FULL)
open(p, 'w', encoding='utf-8').write(s)
print('1. index.html')

# ── 2. router ────────────────────────────────────────────────────────────
p = 'src/router/index.ts'
s = open(p, encoding='utf-8').read()
s = s.replace('白泽 · 数据智能平台', BRAND_FULL)
open(p, 'w', encoding='utf-8').write(s)
print('2. router')

# ── 3. init_db ───────────────────────────────────────────────────────────
p = 'server/init_db.py'
s = open(p, encoding='utf-8').read()
s = s.replace('白泽 · 数据智能平台', BRAND_FULL)
s = s.replace("DEFAULT '白泽'", f"DEFAULT '{BRAND_SHORT}'")
s = s.replace("system_short_name = '白泽'", f"system_short_name = '{BRAND_SHORT}'")
open(p, 'w', encoding='utf-8').write(s)
print('3. init_db')

# ── 4. 既有库更名 SQL（与巡检旧名一起覆盖） ──────────────────────────────
p = 'server/init_db.py'
s = open(p, encoding='utf-8').read()
old = """        # 品牌更名（白泽 · 数据智能平台）：既有库幂等更新，仅当仍为旧名
        cur.execute(
            "UPDATE system_config SET system_name = '白泽 · 数据智能平台', "
            " system_short_name = '白泽' "
            "WHERE system_name LIKE '巡检%' OR system_short_name IN ('巡检管理')")"""
new = """        # 品牌更名（BKB · 数据智能平台）：既有库幂等更新，覆盖旧名和中间态
        cur.execute(
            "UPDATE system_config SET system_name = 'BKB · 数据智能平台', "
            " system_short_name = 'BKB' "
            "WHERE system_name LIKE '巡检%' OR system_name LIKE '%白泽%' "
            "   OR system_short_name IN ('巡检管理', '白泽')")"""
assert old in s, 'init_db migration block not found'
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print('4. init_db migration block')

# ── 5. favicon ───────────────────────────────────────────────────────────
fav = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a0a1a"/>
      <stop offset="1" stop-color="#1a1a2e"/>
    </linearGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#c0c0c0"/>
      <stop offset="1" stop-color="#606060"/>
    </linearGradient>
    <linearGradient id="glow" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#a78bfa"/>
      <stop offset="1" stop-color="#6366f1"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#bg)"/>
  <!-- BKB 法杖造型：竖杖 + 顶部宝石 -->
  <rect x="28" y="14" width="8" height="34" rx="3" fill="url(#bar)"/>
  <rect x="26" y="10" width="12" height="8" rx="2" fill="url(#glow)"/>
  <!-- 底座横杆 -->
  <rect x="16" y="44" width="32" height="5" rx="2.5" fill="url(#bar)"/>
  <!-- 魔法纹路 -->
  <path d="M32 18 L32 44" stroke="#a78bfa" stroke-width="1.5" opacity="0.6"/>
  <circle cx="32" cy="14" r="3" fill="#e0e0ff"/>
  <text x="32" y="57" text-anchor="middle" font-size="8" font-weight="700"
        fill="#a78bfa" font-family="monospace">BKB</text>
</svg>
'''
open('public/favicon.svg', 'w', encoding='utf-8').write(fav)
print('5. favicon')

# ── 6. docs ──────────────────────────────────────────────────────────────
for dp in ['docs/user-guide/ai/assistant.md',
           'docs/user-guide/ai/trace-analysis.md',
           'docs/design/AI能力总览与长任务稳定性.md',
           'docs/design/AI执行审计能力说明.md']:
    if not os.path.exists(dp): continue
    ds = open(dp, encoding='utf-8').read()
    if '白泽' in ds:
        ds = ds.replace('白泽', 'BKB')
        open(dp, 'w', encoding='utf-8').write(ds)
        print(f'6. doc: {dp}')
print('all done')
