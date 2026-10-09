import { ref, onMounted, onUnmounted, type Ref } from 'vue'

/** ECharts 懒加载封装：echarts 已在依赖里（md-editor 同源），动态 import
 *  避免进主包。init 失败（jsdom/无 canvas 测试环境）静默降级为 no-op，
 *  组件层不因此报错。setOption 前必须等 ready。 */
export function useEcharts(el: Ref<HTMLElement | null>) {
  let chart: any = null
  let disposed = false
  const ready = ref(false)
  const pending: Array<[string, (params: any) => void]> = []

  onMounted(async () => {
    try {
      const echarts = await import('echarts')
      // 动态 import 解析前组件可能已卸载——此时不再 init（否则图表建在
      // 已分离元素上且永不 dispose）
      if (disposed || !el.value) return
      chart = echarts.init(el.value)
      ready.value = true
      for (const [event, cb] of pending.splice(0)) chart.on(event, cb)
    } catch (e) {
      console.warn('[useEcharts] init skipped:', e)
    }
  })
  onUnmounted(() => {
    disposed = true
    chart?.dispose()
    chart = null
    pending.length = 0
  })

  return {
    ready,
    setOption(opt: any) { chart?.setOption(opt) },
    /** 事件订阅：ready 前调用则挂起队列，init 完成后统一注册；卸载时随
     *  chart.dispose 一并清理。 */
    on(event: string, cb: (params: any) => void) {
      if (chart) chart.on(event, cb)
      else pending.push([event, cb])
    },
  }
}
