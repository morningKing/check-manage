/**
 * Home Widget 组件统一导出
 */

export { default as WelcomeWidget } from './WelcomeWidget.vue'
export { default as StatsWidget } from './StatsWidget.vue'
export { default as QuickLinksWidget } from './QuickLinksWidget.vue'
export { default as SystemInfoWidget } from './SystemInfoWidget.vue'

import type { WidgetType } from '@/types'
import WelcomeWidget from './WelcomeWidget.vue'
import StatsWidget from './StatsWidget.vue'
import QuickLinksWidget from './QuickLinksWidget.vue'
import SystemInfoWidget from './SystemInfoWidget.vue'

/**
 * Widget 类型到组件的映射
 */
export const widgetComponentMap: Record<WidgetType, any> = {
  welcome: WelcomeWidget,
  stats: StatsWidget,
  'quick-links': QuickLinksWidget,
  'system-info': SystemInfoWidget,
  'custom-markdown': SystemInfoWidget, // 使用相同的渲染组件
  'data-card': null, // 高级组件，待后续实现
}