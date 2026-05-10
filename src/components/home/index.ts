/**
 * Home Widget 组件统一导出
 */

export { default as WelcomeWidget } from './WelcomeWidget.vue'
export { default as StatsWidget } from './StatsWidget.vue'
export { default as QuickLinksWidget } from './QuickLinksWidget.vue'
export { default as SystemInfoWidget } from './SystemInfoWidget.vue'
export { default as MarkdownWidget } from './MarkdownWidget.vue'
export { default as DataCardWidget } from './DataCardWidget.vue'

import type { WidgetType } from '@/types'
import WelcomeWidget from './WelcomeWidget.vue'
import StatsWidget from './StatsWidget.vue'
import QuickLinksWidget from './QuickLinksWidget.vue'
import SystemInfoWidget from './SystemInfoWidget.vue'
import MarkdownWidget from './MarkdownWidget.vue'
import DataCardWidget from './DataCardWidget.vue'

/**
 * Widget 类型到组件的映射
 */
export const widgetComponentMap: Record<WidgetType, any> = {
  welcome: WelcomeWidget,
  stats: StatsWidget,
  'quick-links': QuickLinksWidget,
  'system-info': SystemInfoWidget,
  'custom-markdown': MarkdownWidget,
  'data-card': DataCardWidget,
}