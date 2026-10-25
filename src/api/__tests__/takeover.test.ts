// 回归：takeover 三个请求必须 { silent: true } 传给 @/utils/request。
// request.ts 的响应拦截器对非 2xx 且未 silent 的请求会先弹全局 ElMessage.error
// 再 reject——接管页是 3s 轮询 + 页面自绘 409 内联警告 / 404 错误卡，不 silent
// 意味着后端一抖动每 3 秒弹一次「网络连接失败」、409 出现 toast + 内联条双重提示。
// 页面测试 mock 了整个 api 模块，request 层根本不执行，所以这层契约只能在
// api 层测试里锁定（kefuPublic.clickKefuFaq 是既有先例）。
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
}))
import { get, post } from '@/utils/request'
import { getTakeoverSession, getTakeoverMessages, sendTakeoverMessage } from '../takeover'

beforeEach(() => vi.clearAllMocks())

describe('api/takeover 必须以 silent 配置发请求（避免全局 toast 与轮询轰炸）', () => {
  it('session 查询带 silent 配置，token 正确编码', async () => {
    await getTakeoverSession('t 1')
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/external/takeover/session?t=t%201', undefined, { silent: true })
  })

  it('messages 轮询带 silent 配置', async () => {
    await getTakeoverMessages('t')
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/external/takeover/session/messages?t=t', undefined, { silent: true })
  })

  it('发送消息（POST）带 silent 配置，prompt 走请求体', async () => {
    await sendTakeoverMessage('t', '你好')
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/external/takeover/session/messages?t=t', { prompt: '你好' }, { silent: true })
  })
})
