import { get } from '@/utils/request'

export interface GraphNode {
  id: string
  label: string
  collection: string
  collectionLabel: string
  data?: Record<string, any>
}

export interface GraphEdge {
  source: string
  target: string
  label: string
  relType: 'relation' | 'reference' | 'quoteSelect'
}

export interface RelationGraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  centerId: string
}

export function getRelationGraph(collection: string, recordId: string): Promise<RelationGraphData> {
  return get<RelationGraphData>(`/relation-graph/${collection}/${recordId}`)
}
