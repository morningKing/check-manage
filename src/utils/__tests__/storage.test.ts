import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  STORAGE_KEYS,
  setStorage,
  getStorage,
  removeStorage,
  clearAppStorage,
  isStorageAvailable,
} from '../storage'

describe('Storage Utils', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  describe('STORAGE_KEYS', () => {
    it('所有 key 以 check-manage: 开头', () => {
      Object.values(STORAGE_KEYS).forEach((key) => {
        expect(key).toMatch(/^check-manage:/)
      })
    })
  })

  describe('setStorage / getStorage', () => {
    it('正常存取字符串', () => {
      setStorage('test-key', 'hello')
      expect(getStorage('test-key', '')).toBe('hello')
    })

    it('正常存取对象', () => {
      const obj = { name: '测试', count: 42 }
      setStorage('test-obj', obj)
      expect(getStorage('test-obj', {})).toEqual(obj)
    })

    it('正常存取数组', () => {
      const arr = [1, 2, 3]
      setStorage('test-arr', arr)
      expect(getStorage('test-arr', [])).toEqual(arr)
    })

    it('正常存取布尔值', () => {
      setStorage('test-bool', true)
      expect(getStorage('test-bool', false)).toBe(true)
    })

    it('key 不存在时返回默认值', () => {
      expect(getStorage('nonexistent', 'default')).toBe('default')
    })

    it('key 不存在时返回默认对象', () => {
      expect(getStorage('nonexistent', { a: 1 })).toEqual({ a: 1 })
    })

    it('JSON 解析失败时返回默认值', () => {
      localStorage.setItem('bad-json', '{invalid json')
      expect(getStorage('bad-json', 'fallback')).toBe('fallback')
    })
  })

  describe('removeStorage', () => {
    it('移除已有 key', () => {
      setStorage('to-remove', 'value')
      expect(getStorage('to-remove', '')).toBe('value')

      removeStorage('to-remove')
      expect(getStorage('to-remove', 'gone')).toBe('gone')
    })

    it('移除不存在的 key 不报错', () => {
      expect(() => removeStorage('nonexistent')).not.toThrow()
    })
  })

  describe('clearAppStorage', () => {
    it('只清除 check-manage: 前缀的 key', () => {
      localStorage.setItem('check-manage:token', '"t1"')
      localStorage.setItem('check-manage:menus', '[]')
      localStorage.setItem('other-app:data', '"keep"')
      localStorage.setItem('random-key', '"keep"')

      clearAppStorage()

      expect(localStorage.getItem('check-manage:token')).toBeNull()
      expect(localStorage.getItem('check-manage:menus')).toBeNull()
      expect(localStorage.getItem('other-app:data')).toBe('"keep"')
      expect(localStorage.getItem('random-key')).toBe('"keep"')
    })

    it('空 storage 时不报错', () => {
      expect(() => clearAppStorage()).not.toThrow()
    })
  })

  describe('isStorageAvailable', () => {
    it('正常环境返回 true', () => {
      expect(isStorageAvailable()).toBe(true)
    })
  })
})
