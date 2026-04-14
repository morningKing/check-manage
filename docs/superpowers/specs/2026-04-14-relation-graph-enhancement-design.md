# Relation Graph Enhancement Design

**Date:** 2026-04-14
**Status:** Approved

## Problem

The PageConfig relation graph currently has poor UX:
1. Edges show only a simple label — no indication of which field creates the relationship or what type it is
2. All nodes start at position (0,0) with no layout algorithm — they overlap
3. No legend explaining the color coding
4. No minimap for navigation in larger graphs

## Solution

Enhance the existing `PageConfigRelationGraph.vue` component with three improvements:

### B. Edge Labels with Field Names and Relation Types

- Edge labels display "字段名 · 关联类型" (e.g., "关联客户 · relation")
- Color-coded edges: blue (#409EFF) for relation, green (#67C23A) for reference, orange (#E6A23C) for quoteSelect
- Line styles: relation = dashed + animated, reference = solid, quoteSelect = dotted
- Arrow markers pointing to the target page
- Custom edge component to render styled label badges

### C. Dagre Hierarchical Layout

- Install `@dagrejs/dagre` for layout calculation
- Left-to-right (LR) direction
- Auto-calculate node positions after data loads
- Current page node: blue border + blue header + glow shadow (highlighted)
- Other nodes: gray border + gray header
- Proper spacing (nodesep: 80, ranksep: 200) to prevent overlap

### D. Legend + Interaction Enhancements

- Top-right legend panel showing three relation types with color/line-style samples
- VueFlow MiniMap component for navigation
- Hover on a node highlights connected edges (dims unrelated edges)
- Click non-current nodes to navigate (existing behavior, preserved)

## Technical Details

### Dependencies
- `@dagrejs/dagre` — new dependency for layout
- `@vue-flow/minimap` — already available with @vue-flow/core

### Files Modified
1. `src/components/PageConfigRelationGraph.vue` — main component rewrite
2. `src/api/page.ts` — update `RelationEdge` type (already has `field` and `type`)

### Backend
No backend changes needed. The API already returns `field`, `label`, and `type` on each edge.

### Node Data Structure
```typescript
{
  id: string
  type: 'custom'
  position: { x: number, y: number }  // calculated by dagre
  data: {
    name: string
    fields: number
    id: string
    isCurrent: boolean  // true for the current pageId
  }
}
```

### Edge Data Structure
```typescript
{
  id: string
  source: string
  target: string
  type: 'custom'  // custom edge component
  data: {
    fieldName: string   // e.g., "关联客户"
    relationType: string // "relation" | "reference" | "quoteSelect"
  }
}
```

### Layout Algorithm
```
1. Create dagre graph with rankdir: 'LR'
2. Add all nodes with estimated dimensions (180x80)
3. Add all edges
4. Run dagre.layout()
5. Map dagre positions back to VueFlow node positions
```
