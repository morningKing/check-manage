import type { InjectionKey, Ref } from 'vue'

/**
 * DynamicForm 通过 provide 下发当前表单所属的数据页 collection。
 * 文件 / 图片上传控件 inject 它，上传时一并发给后端，
 * 后端据此按「该数据页的写权限」鉴权（支持被授权的访客 / 自定义角色）。
 */
export const DYNAMIC_FORM_COLLECTION: InjectionKey<Ref<string | undefined>> =
  Symbol('dynamicFormCollection')
