/**
 * 生成 1 万条巡检用例测试数据的导入文件
 *
 * 用法:
 *   node scripts/generate-test-data.mjs --template <模板文件路径>
 *
 * 说明:
 *   先从系统页面"下载导入模板"获取 .xlsx 模板文件，
 *   脚本读取模板表头，自动填充随机测试数据。
 *
 * 输出: scripts/巡检用例_1万条.xlsx
 */

import XLSX from 'xlsx'
import { fileURLToPath } from 'url'
import path from 'path'
import fs from 'fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const TOTAL = 10000

// 每次运行生成唯一批次号，避免主键冲突
const batchId = Date.now().toString(36).toUpperCase()

// ==================== 解析参数 ====================

function parseArgs() {
  const args = process.argv.slice(2)
  let templatePath = null
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--template' && args[i + 1]) {
      templatePath = args[i + 1]
      break
    }
  }
  return { templatePath }
}

const { templatePath } = parseArgs()

if (!templatePath) {
  console.error('用法: node scripts/generate-test-data.mjs --template <模板文件路径>')
  console.error('')
  console.error('请先从系统页面"下载导入模板"获取 .xlsx 模板文件，然后指定路径。')
  process.exit(1)
}

if (!fs.existsSync(templatePath)) {
  console.error(`模板文件不存在: ${templatePath}`)
  process.exit(1)
}

// ==================== 读取模板表头 ====================

const templateWb = XLSX.readFile(templatePath)
const templateWs = templateWb.Sheets[templateWb.SheetNames[0]]
const templateRows = XLSX.utils.sheet_to_json(templateWs, { header: 1 })

if (!templateRows.length || !templateRows[0].length) {
  console.error('模板文件第一个 sheet 中没有表头')
  process.exit(1)
}

const headers = templateRows[0].map(String)
console.log(`从模板读取到 ${headers.length} 个字段: ${headers.join(', ')}`)

// 读取字段说明 sheet（如有），建立 label → fieldName 映射
const fieldNameMap = new Map()
if (templateWb.SheetNames.length >= 2) {
  const guideWs = templateWb.Sheets[templateWb.SheetNames[1]]
  const guideRows = XLSX.utils.sheet_to_json(guideWs, { header: 1 })
  // 第一行是表头: ['字段名称', '字段标识', '类型', ...]
  for (let i = 1; i < guideRows.length; i++) {
    const row = guideRows[i]
    if (row[0] && row[1]) {
      fieldNameMap.set(String(row[0]), { fieldName: String(row[1]), type: String(row[2] || '') })
    }
  }
}

// ==================== 随机数据池 ====================

const textPool = [
  '测试项目-Alpha', '测试项目-Beta', '测试项目-Gamma', '检查项-A',
  '检查项-B', '功能验证', '性能评估', '安全审查', '定期维护', '专项排查',
]
const textareaPool = [
  '按规范逐项检查，确保各项指标正常',
  '设备运行状态正常，无异常告警',
  '1.现场检查 2.记录数据 3.提交报告',
  '需关注重点区域，发现异常及时上报',
  '所有指标均在正常范围内',
]
const numberPool = [1, 2, 5, 10, 20, 50, 100, 200, 500, 999]
const personPool = ['张三', '李四', '王五', '赵六', '陈七', '刘八', '周九', '吴十']

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

// ==================== 按字段类型生成随机值 ====================

function generateValue(header, index) {
  const idx = String(index).padStart(5, '0')
  const meta = fieldNameMap.get(header)
  const type = meta?.type || ''

  // 自动时间戳、自增序列 — 留空，系统自动生成
  if (type.includes('自动时间戳') || type.includes('自增序列') || type.includes('无需填写')) {
    return ''
  }

  // 单选：从可选值中随机取
  if (type === '单选') {
    // 尝试从字段说明 sheet 读取可选值
    const optStr = getFieldOptions(header)
    if (optStr) {
      const opts = optStr.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)
      return pick(opts)
    }
    return pick(textPool)
  }

  // 多选
  if (type.includes('多选')) {
    const optStr = getFieldOptions(header)
    if (optStr) {
      const opts = optStr.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)
      const count = Math.min(1 + Math.floor(Math.random() * 3), opts.length)
      const shuffled = opts.sort(() => Math.random() - 0.5)
      return shuffled.slice(0, count).join('、')
    }
    return pick(textPool)
  }

  // 数字
  if (type === '数字') {
    return pick(numberPool)
  }

  // 日期
  if (type.includes('日期时间')) {
    const d = new Date(2024, Math.floor(Math.random() * 12), 1 + Math.floor(Math.random() * 28),
      Math.floor(Math.random() * 24), Math.floor(Math.random() * 60))
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:00`
  }
  if (type.includes('日期')) {
    const d = new Date(2024, Math.floor(Math.random() * 12), 1 + Math.floor(Math.random() * 28))
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
  }

  // 多行文本
  if (type === '多行文本') {
    return pick(textareaPool)
  }

  // 关联 / 引用 — 留空
  if (type.includes('关联') || type.includes('引用')) {
    return ''
  }

  // 默认：文本
  return `${header}-${batchId}-${idx}`
}

function p(n) {
  return String(n).padStart(2, '0')
}

function getFieldOptions(header) {
  if (templateWb.SheetNames.length < 2) return null
  const guideWs = templateWb.Sheets[templateWb.SheetNames[1]]
  const guideRows = XLSX.utils.sheet_to_json(guideWs, { header: 1 })
  for (let i = 1; i < guideRows.length; i++) {
    if (String(guideRows[i][0]) === header && guideRows[i][4]) {
      return String(guideRows[i][4])
    }
  }
  return null
}

// ==================== 数据生成 ====================

const rows = []
for (let i = 1; i <= TOTAL; i++) {
  rows.push(headers.map((header) => generateValue(header, i)))
}

// ==================== 写入 Excel ====================

const wsData = [headers, ...rows]
const ws = XLSX.utils.aoa_to_sheet(wsData)

ws['!cols'] = headers.map((h) => ({ wch: Math.max(h.length * 2 + 4, 16) }))

const wb = XLSX.utils.book_new()
XLSX.utils.book_append_sheet(wb, ws, '导入数据')

const outPath = path.join(__dirname, '巡检用例_1万条.xlsx')
XLSX.writeFile(wb, outPath)

console.log(`已生成 ${TOTAL} 条数据 → ${outPath}`)
console.log(`文件大小: ${(fs.statSync(outPath).size / 1024 / 1024).toFixed(2)} MB`)
