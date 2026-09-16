import { onMounted, onUnmounted, ref } from 'vue'

/**
 * 跟踪 `<html>` 元素上的 `dark` class（由 stores/app.ts 的 applyTheme 切换，
 * 含 auto 模式跟随系统），供不便依赖 store 的叶子组件做主题感知。
 *
 * 之所以用 MutationObserver 而不是复用 store 的 themeMode：auto 模式下系统
 * 主题变化会直接改 class，观察 class 是唯一与「实际生效主题」保持一致的信号。
 */
export function useDarkMode() {
  const isDark = ref(
    typeof document !== 'undefined'
      && document.documentElement.classList.contains('dark'),
  )

  const observer = new MutationObserver(() => {
    isDark.value = document.documentElement.classList.contains('dark')
  })

  onMounted(() => {
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    })
  })
  onUnmounted(() => observer.disconnect())

  return isDark
}
