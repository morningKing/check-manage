import type { AiTraceMetadata } from '@/types/aiChat'

export function traceLinkFor(metadata?: AiTraceMetadata | null): AiTraceMetadata | undefined {
  return metadata?.traceUrl ? metadata : undefined
}
