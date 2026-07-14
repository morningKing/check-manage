/**
 * 文件/图片上传的类型约束校验
 *
 * 抽成独立纯函数供 FileUpload.vue / ImageUpload.vue 共用，
 * 避免两处控件各写一套扩展名匹配逻辑。
 */

/**
 * 取文件名的扩展名（含前导点，小写）；没有扩展名时返回空字符串
 */
export function getFileExtension(filename: string): string {
  const idx = filename.lastIndexOf('.')
  if (idx === -1 || idx === filename.length - 1) return ''
  return filename.slice(idx).toLowerCase()
}

/**
 * 判断文件名是否在允许的扩展名列表内。
 * allowedExtensions 为空/未配置时视为不限制。
 */
export function isExtensionAllowed(filename: string, allowedExtensions: string[] | undefined): boolean {
  if (!allowedExtensions || allowedExtensions.length === 0) return true
  return allowedExtensions.includes(getFileExtension(filename))
}
