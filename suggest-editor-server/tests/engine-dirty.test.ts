import test from 'node:test'
import assert from 'node:assert/strict'
import { takeDirtyBlocks, MAX_DIRTY_BLOCKS_PER_RUN } from '../src/engine'

function block(i: number) {
  return { start: i, end: i + 1, text: `p${i}`, hash: `h${i}` }
}

test('dirty blocks are capped per run', () => {
  const blocks = Array.from({ length: 100 }, (_, i) => block(i))
  const dirty = takeDirtyBlocks(blocks, new Set(), MAX_DIRTY_BLOCKS_PER_RUN)
  assert.equal(dirty.length, MAX_DIRTY_BLOCKS_PER_RUN)
  assert.deepEqual(dirty.map((b) => b.text), blocks.slice(0, 40).map((b) => b.text))
})

test('already analyzed blocks are skipped before capping', () => {
  const blocks = Array.from({ length: 50 }, (_, i) => block(i))
  const analyzed = new Set(blocks.slice(0, 45).map((b) => b.hash))
  const dirty = takeDirtyBlocks(blocks, analyzed, 40)
  assert.deepEqual(dirty.map((b) => b.text), ['p45', 'p46', 'p47', 'p48', 'p49'])
})
