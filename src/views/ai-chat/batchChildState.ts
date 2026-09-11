export function isBatchChildAutomatic(status: string) {
  return status === 'pending' || status === 'running'
}

export function batchStatusLabel(status: string) {
  return ({
    pending: '待运行', running: '正在运行', completed: '已完成',
    partial: '部分失败', failed: '失败', cancelled: '已取消',
  } as Record<string, string>)[status] || status
}
