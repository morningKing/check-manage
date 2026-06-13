/** 识别后端乐观锁版本冲突（HTTP 409 + code=VERSION_CONFLICT）。 */
export function isVersionConflict(err: any): boolean {
  const r = err?.response
  return !!r && r.status === 409 && r.data?.code === 'VERSION_CONFLICT'
}

/** 统一的版本冲突提示文案。 */
export function conflictMessage(): string {
  return '数据已被其他用户修改，请刷新后重试'
}
