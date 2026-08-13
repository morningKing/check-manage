/**
 * vue-element-plus-x 的测试桩。
 * 真实包的 ES 产物直接 import './index.css'，在 vitest 的 Node ESM 环境下
 * 会抛 "Unknown file extension .css"；组件测试只关心渲染结构，用轻量桩替代。
 */
import { defineComponent, h } from 'vue'

export const Bubble = defineComponent({
  name: 'Bubble',
  render() {
    return h('div', { class: 'stub-bubble' }, this.$slots.content?.() ?? [])
  },
})

export const Thinking = defineComponent({
  name: 'Thinking',
  props: { content: String, status: String, autoCollapse: Boolean },
  render() {
    return h('div', { class: 'stub-thinking' }, this.content ?? '')
  },
})
