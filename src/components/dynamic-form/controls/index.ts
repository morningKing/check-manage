/**
 * 表单控件组件注册
 *
 * 集中管理所有表单控件，提供控件映射和动态组件加载
 */

import type { Component } from 'vue'
import type { ControlType } from '@/types'

// 导入所有控件组件
import TextInput from './TextInput.vue'
import TextArea from './TextArea.vue'
import RichTextEditor from './RichTextEditor.vue'
import NumberInput from './NumberInput.vue'
import SelectInput from './SelectInput.vue'
import MultiSelect from './MultiSelect.vue'
import DatePicker from './DatePicker.vue'
import RadioGroup from './RadioGroup.vue'
import CheckboxGroup from './CheckboxGroup.vue'
import FileUpload from './FileUpload.vue'
import ImageUpload from './ImageUpload.vue'
import RelationSelect from './RelationSelect.vue'
import ReferenceSelect from './ReferenceSelect.vue'
import AutoTimestamp from './AutoTimestamp.vue'
import AutoSequence from './AutoSequence.vue'
import QuoteSelect from './QuoteSelect.vue'

/**
 * 控件类型到组件的映射
 *
 * 用于根据 controlType 动态渲染对应的控件组件
 */
export const controlComponentMap: Record<ControlType, Component> = {
  text: TextInput,
  textarea: TextArea,
  richText: RichTextEditor,
  number: NumberInput,
  select: SelectInput,
  multiSelect: MultiSelect,
  date: DatePicker,
  datetime: DatePicker,
  radio: RadioGroup,
  checkbox: CheckboxGroup,
  file: FileUpload,
  image: ImageUpload,
  relation: RelationSelect,
  reference: ReferenceSelect,
  autoTimestamp: AutoTimestamp,
  autoSequence: AutoSequence,
  quoteSelect: QuoteSelect
}

/**
 * 根据控件类型获取对应组件
 *
 * @param controlType - 控件类型
 * @returns 对应的 Vue 组件
 */
export function getControlComponent(controlType: ControlType): Component {
  return controlComponentMap[controlType] || TextInput
}

/**
 * 获取控件的默认值
 *
 * @param controlType - 控件类型
 * @returns 默认值
 */
export function getControlDefaultValue(controlType: ControlType): any {
  switch (controlType) {
    case 'multiSelect':
    case 'checkbox':
      return []
    case 'relation':
      return []
    case 'quoteSelect':
      return []
    case 'number':
    case 'reference':
    case 'autoTimestamp':
    case 'autoSequence':
      return null
    case 'file':
    case 'image':
      return []
    case 'richText':
      return ''
    default:
      return ''
  }
}

// 导出所有控件组件
export {
  TextInput,
  TextArea,
  RichTextEditor,
  NumberInput,
  SelectInput,
  MultiSelect,
  DatePicker,
  RadioGroup,
  CheckboxGroup,
  FileUpload,
  ImageUpload,
  RelationSelect,
  ReferenceSelect,
  AutoTimestamp,
  AutoSequence,
  QuoteSelect
}
