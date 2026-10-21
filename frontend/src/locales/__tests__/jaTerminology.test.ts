/**
 * T13 · 日文用词统一 ——「文書」→「ドキュメント」（以多数为准）。
 *
 * 现状：ja.ts 中 ドキュメント 14 处、文書 5 处混用（knowledge.tags.tech 与
 * refPicker 四个词条）。统一为多数词「ドキュメント」。
 *
 * 判别点：任一词条改回「文書」→ 第一断言红并点名路径。
 */
import { describe, it, expect } from 'vitest'
import ja from '@/locales/ja'

type Messages = Record<string, unknown>

/** 展开叶子键路径并筛出命中 term 的条目 */
function pathsContaining(node: unknown, term: string, pre = ''): string[] {
  const out: string[] = []
  if (typeof node === 'string') {
    if (node.includes(term)) out.push(pre || '(root)')
    return out
  }
  if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node as Messages)) {
      out.push(...pathsContaining(v, term, pre ? `${pre}.${k}` : k))
    }
  }
  return out
}

describe('ja · 文档用词统一（ドキュメント）', () => {
  it('ja 全量词条不再出现「文書」', () => {
    const hits = pathsContaining(ja, '文書')
    expect(hits.join(', '), `「文書」残留 ${hits.length} 处（统一用词：ドキュメント）`).toBe('')
  })

  it('原 5 处词条落位为「ドキュメント」', () => {
    expect(ja.knowledge.tags.tech).toBe('技術ドキュメント')
    expect(ja.refPicker.heading).toBe('ナレッジベースドキュメントを引用')
    expect(ja.refPicker.empty).toBe('一致するドキュメントがありません')
    expect(ja.refPicker.error).toBe('ドキュメントの読み込みに失敗しました。再試行してください')
    expect(ja.refPicker.inputHint).toContain('ドキュメント引用')
  })
})
