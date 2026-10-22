import test from 'node:test'
import assert from 'node:assert/strict'
import * as Y from 'yjs'
import { flattenFragment } from '../src/flatten'

function tableFragment(cells: string[][]): Y.XmlFragment {
  const doc = new Y.Doc()
  const frag = doc.getXmlFragment('default')
  const table = new Y.XmlElement('table')
  cells.forEach((rowCells, rowIndex) => {
    const row = new Y.XmlElement('tableRow')
    rowCells.forEach((value, cellIndex) => {
      const cell = new Y.XmlElement(rowIndex === 0 ? 'tableHeader' : 'tableCell')
      const p = new Y.XmlElement('paragraph')
      const text = new Y.XmlText()
      text.insert(0, value)
      p.insert(0, [text])
      cell.insert(0, [p])
      row.insert(cellIndex, [cell])
    })
    table.insert(rowIndex, [row])
  })
  frag.insert(0, [table])
  return frag
}

test('table serializes to markdown rows and keeps segments', () => {
  const flat = flattenFragment(tableFragment([['设备类型', '数量'], ['9800M12', '3']]))
  assert.match(flat.text, /\| 设备类型 \| 数量 \|/)
  assert.match(flat.text, /\| --- \| --- \|/)
  assert.match(flat.text, /\| 9800M12 \| 3 \|/)
  assert.equal(flat.blocks.length, 1)
  assert.ok(flat.segments.some((s) => s.yText.toString() === '9800M12'))
})

test('editing a cell changes the table block hash', () => {
  const a = flattenFragment(tableFragment([['设备类型'], ['9800M12']])).blocks[0].hash
  const b = flattenFragment(tableFragment([['设备类型'], ['5000M12']])).blocks[0].hash
  assert.notEqual(a, b)
})

test('figure serializes caption and alt, image-only figure has empty text', () => {
  const doc = new Y.Doc()
  const frag = doc.getXmlFragment('default')
  const figure = new Y.XmlElement('figure')
  const image = new Y.XmlElement('image')
  image.setAttribute('alt', '拓扑')
  const caption = new Y.XmlElement('figureCaption')
  const text = new Y.XmlText()
  text.insert(0, '图 1 拓扑')
  caption.insert(0, [text])
  figure.insert(0, [image, caption])
  frag.insert(0, [figure])
  const flat = flattenFragment(frag)
  assert.match(flat.text, /\[图片\] 拓扑/)
  assert.match(flat.text, /图 1 拓扑/)
  assert.equal(flat.blocks.length, 1)
})
