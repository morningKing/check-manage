import { get, post, patch, del } from '@/utils/request'

export interface KefuInstance { id: string; slug: string; name: string; enabled: boolean }
export interface KefuFaq {
  id: string; instance_id: string; question: string; answer: string
  category: string | null; sort_order: number; click_count: number; enabled: boolean
}

export function listInstances() { return get<{ instances: KefuInstance[] }>('/admin/kefu/instances') }
export function listFaq(iid: string) { return get<{ items: KefuFaq[] }>(`/admin/kefu/instances/${iid}/faq`) }
export function createFaq(iid: string, data: Partial<KefuFaq>) { return post<KefuFaq>(`/admin/kefu/instances/${iid}/faq`, data) }
export function updateFaq(iid: string, fid: string, data: Partial<KefuFaq>) { return patch<KefuFaq>(`/admin/kefu/instances/${iid}/faq/${fid}`, data) }
export function deleteFaq(iid: string, fid: string) { return del(`/admin/kefu/instances/${iid}/faq/${fid}`) }
export function reorderFaq(iid: string, order: string[]) { return patch(`/admin/kefu/instances/${iid}/faq/reorder`, { order }) }
