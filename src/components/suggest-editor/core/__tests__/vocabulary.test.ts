import { describe, expect, it } from "vitest"
import { isSentenceEndBehind, isWordEndAhead } from "../vocabulary"

describe("补全触发边界（IDE 行为：词尾/句尾才提示）", () => {
  describe("isWordEndAhead —— 光标后紧跟词字符即「词中」，候选不弹", () => {
    it("词中（汉字/字母/连字符词）返回 false", () => {
      expect(isWordEndAhead("储系统")).toBe(false) // 分布式存|储系统
      expect(isWordEndAhead("tion")).toBe(false) // comple|tion
      expect(isWordEndAhead("-board")).toBe(false) // cfg-|board（连字符算词字符）
    })

    it("词尾（段尾/空白/标点之后）返回 true", () => {
      expect(isWordEndAhead("")).toBe(true) // 段尾
      expect(isWordEndAhead("，支撑核心业务")).toBe(true)
      expect(isWordEndAhead(" 是未来")).toBe(true)
      expect(isWordEndAhead("。")).toBe(true)
    })
  })

  describe("isSentenceEndBehind —— 光标前是句读即「句尾」，续写在此提示", () => {
    it("句读（。！？；…：）后返回 true", () => {
      expect(isSentenceEndBehind("系统上线。")).toBe(true)
      expect(isSentenceEndBehind("结果如何？")).toBe(true)
      expect(isSentenceEndBehind("具体情况如下：")).toBe(true)
      expect(isSentenceEndBehind("等等……")).toBe(true)
    })

    it("句中（无句读/逗号停顿/词中）返回 false", () => {
      expect(isSentenceEndBehind("")).toBe(false)
      expect(isSentenceEndBehind("系统上线，")).toBe(false) // 逗号是句中停顿
      expect(isSentenceEndBehind("核心业务")).toBe(false)
    })
  })
})
