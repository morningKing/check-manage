import { ref, onMounted, onUnmounted, type Ref } from 'vue'

/** ECharts 懒加载封装：echarts 已在依赖里（md-editor 同源），动态 import
 *  避免进主包。init 失败（jsdom/无 canvas 测试环境）静默降级为 no-op，
 *  组件层不因此报错。setOption 前必须等 ready。 */
export function useEcharts(el: Ref<HTMLElement | null>) {
  let chart: any = null
  const ready = ref(false)

  onMounted(async () => {
    try {
      const echarts = await import('echarts')
      if (el.value) {
        chart = echarts.init(el.value)
        ready.value = true
      }
    } catch (e) {
      console.warn('[useEcharts] init skipped:', e)
    }
  })
  onUnmounted(() => { chart?.dispose(); chart = null })

  return {
    ready,
    setOption(opt: any) { chart?.setOption(opt) },
  }
}
