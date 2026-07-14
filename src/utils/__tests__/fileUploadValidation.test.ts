import { describe, it, expect } from 'vitest'
import { getFileExtension, isExtensionAllowed } from '../fileUploadValidation'

describe('getFileExtension', () => {
  it('返回小写、含前导点的扩展名', () => {
    expect(getFileExtension('report.PDF')).toBe('.pdf')
    expect(getFileExtension('a.b.docx')).toBe('.docx')
  })

  it('没有扩展名时返回空字符串', () => {
    expect(getFileExtension('README')).toBe('')
  })

  it('以点结尾（无实际扩展名内容）时返回空字符串', () => {
    expect(getFileExtension('file.')).toBe('')
  })
})

describe('isExtensionAllowed', () => {
  it('allowedExtensions 未配置或为空数组时不限制', () => {
    expect(isExtensionAllowed('virus.exe', undefined)).toBe(true)
    expect(isExtensionAllowed('virus.exe', [])).toBe(true)
  })

  it('扩展名在允许列表内时通过', () => {
    expect(isExtensionAllowed('report.pdf', ['.pdf', '.docx'])).toBe(true)
  })

  it('扩展名大小写不同也应匹配', () => {
    expect(isExtensionAllowed('report.PDF', ['.pdf'])).toBe(true)
  })

  it('扩展名不在允许列表内时拒绝', () => {
    expect(isExtensionAllowed('virus.exe', ['.pdf', '.docx'])).toBe(false)
  })

  it('没有扩展名的文件在有限制时被拒绝', () => {
    expect(isExtensionAllowed('README', ['.pdf'])).toBe(false)
  })
})
