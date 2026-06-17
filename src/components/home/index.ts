/**
 * Home Widget 组件统一导出
 */

export { default as WelcomeWidget } from './WelcomeWidget.vue'
export { default as StatsWidget } from './StatsWidget.vue'
export { default as QuickLinksWidget } from './QuickLinksWidget.vue'
export { default as SystemInfoWidget } from './SystemInfoWidget.vue'
export { default as MarkdownWidget } from './MarkdownWidget.vue'
export { default as DataCardWidget } from './DataCardWidget.vue'
export { default as QuickFormWidget } from './QuickFormWidget.vue'
export { default as ChartWidget } from './ChartWidget.vue'
export { default as TodoWidget } from './TodoWidget.vue'
export { default as ActivityWidget } from './ActivityWidget.vue'
export { default as AnnouncementWidget } from './AnnouncementWidget.vue'

import type { WidgetType } from '@/types'
import WelcomeWidget from './WelcomeWidget.vue'
import StatsWidget from './StatsWidget.vue'
import QuickLinksWidget from './QuickLinksWidget.vue'
import SystemInfoWidget from './SystemInfoWidget.vue'
import MarkdownWidget from './MarkdownWidget.vue'
import DataCardWidget from './DataCardWidget.vue'
import QuickFormWidget from './QuickFormWidget.vue'
import ChartWidget from './ChartWidget.vue'
import TodoWidget from './TodoWidget.vue'
import ActivityWidget from './ActivityWidget.vue'
import AnnouncementWidget from './AnnouncementWidget.vue'

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
  'quick-form': QuickFormWidget,
  chart: ChartWidget,
  todo: TodoWidget,
  activity: ActivityWidget,
  announcement: AnnouncementWidget,
}
