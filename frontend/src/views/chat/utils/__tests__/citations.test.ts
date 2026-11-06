/**
 * 正文 [n] → 来源对象：终态/落库的 sources 只保留被引用项但保留原始 rank，
 * 必须按 rank 查；旧历史全部缺 rank 才回退数组位置。
 */
import { describe, expect, it } from 'vitest'
import { findSourceByCitation } from '../citations'
import type { KnowledgeSource } from '@/types/chat'

const B: KnowledgeSource = { filename: 'B.pdf', rank: 2, documentId: 'doc-b' }
const D: KnowledgeSource = { filename: 'D.pdf', rank: 4, documentId: 'doc-d' }

describe('findSourceByCitation', () => {
  it('过滤后 [rank2, rank4]：按 rank 命中，不按数组下标顺移', () => {
    expect(findSourceByCitation([B, D], 2)).toBe(B)
    expect(findSourceByCitation([B, D], 4)).toBe(D)
  })

  it('被过滤掉的序号（rank 1/3）返回 undefined，不误开相邻来源', () => {
    expect(findSourceByCitation([B, D], 1)).toBeUndefined()
    expect(findSourceByCitation([B, D], 3)).toBeUndefined()
  })

  it('旧历史全部缺 rank：回退过滤后数组位置', () => {
    const oldA: KnowledgeSource = { filename: 'A.pdf' }
    const oldB: KnowledgeSource = { filename: 'B.pdf' }
    expect(findSourceByCitation([oldA, oldB], 1)).toBe(oldA)
    expect(findSourceByCitation([oldA, oldB], 2)).toBe(oldB)
    expect(findSourceByCitation([oldA, oldB], 3)).toBeUndefined()
  })

  it('非法序号与空列表返回 undefined', () => {
    expect(findSourceByCitation([B, D], 0)).toBeUndefined()
    expect(findSourceByCitation([B, D], -1)).toBeUndefined()
    expect(findSourceByCitation([B, D], 1.5)).toBeUndefined()
    expect(findSourceByCitation([], 1)).toBeUndefined()
  })
})
