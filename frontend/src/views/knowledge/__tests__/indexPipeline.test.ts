/**
 * 结构化索引错误码 → i18n 映射的判别性测试（竞品批次四）。
 *
 * 变异点：
 * - 删掉任一错误码映射或改成非 i18n 硬编码 → 对应用例红；
 * - 把 DOCUMENT_NOT_FOUND 标为可重试（凭空造出 destructive affordance）→ 红；
 * - 前端词表与后端闭集不同步（新增/删码）→ 闭集对齐用例红；
 * - 未知码回退时泄漏原始文本 → 红。
 */
import { describe, it, expect } from 'vitest'

import type { IndexErrorCode, IndexPipeline } from '@/api/knowledge'
import {
  INDEX_STAGES,
  canRetryIndexError,
  indexErrorMessage,
  indexPipelineSummary,
  indexStageLabel
} from '../indexPipeline'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

type LocalePack = { knowledge: { indexError: Record<string, string>; indexStage: Record<string, string> } }

const PACKS: Array<[string, LocalePack]> = [
  ['zh', zh as LocalePack],
  ['en', en as LocalePack],
  ['ja', ja as LocalePack],
  ['fr', fr as LocalePack]
]

/** 后端闭集的唯一真源（相对路径由 frontend 根上两级到 backend）。 */
const BACKEND_CODES: readonly string[] = [
  'PARSE_PROFILE_INVALID',
  'PARSE_UNSUPPORTED_FORMAT',
  'PARSE_FAILED',
  'CHUNK_EMPTY',
  'CHUNK_FAILED',
  'EMBED_COUNT_MISMATCH',
  'EMBED_FAILED',
  'INDEX_WRITE_FAILED',
  'INDEX_VERIFICATION_FAILED',
  'INDEX_FAILED',
  'DOCUMENT_NOT_FOUND',
  'JOB_SCHEDULE_FAILED',
  'UNEXPECTED'
]

describe('indexPipeline · 四语可操作文案', () => {
  it('四语均有全部错误码与阶段词条，且不是占位/空串', () => {
    for (const [name, pack] of PACKS) {
      for (const code of BACKEND_CODES) {
        const text = pack.knowledge.indexError?.[code]
        expect(typeof text, `${name}.knowledge.indexError.${code}`).toBe('string')
        expect(text?.length, `${name}.knowledge.indexError.${code} 非空`).toBeGreaterThan(0)
        expect(text, `${name} 不得回退成键名`).not.toBe(`knowledge.indexError.${code}`)
      }
      for (const stage of [...INDEX_STAGES, 'unknown']) {
        const text = pack.knowledge.indexStage?.[stage]
        expect(typeof text, `${name}.knowledge.indexStage.${stage}`).toBe('string')
        expect(text?.length).toBeGreaterThan(0)
      }
    }
  })

  it('译文确实是各自语言（en/ja/fr 不得照抄中文）', () => {
    const zhPack = PACKS[0][1]
    for (const [name, pack] of PACKS.slice(1)) {
      for (const code of BACKEND_CODES) {
        expect(pack.knowledge.indexError[code], `${name}.${code} 不得照抄中文`).not.toBe(
          zhPack.knowledge.indexError[code]
        )
      }
    }
  })

  it('阶段闭集固定为 parse/chunk/embed/index', () => {
    expect(INDEX_STAGES).toEqual(['parse', 'chunk', 'embed', 'index'])
  })
})

describe('indexPipeline · 展示映射与安全回退', () => {
  it('未知码走兜底文案，不泄漏后端原始文本', () => {
    const fallback = indexErrorMessage('UNEXPECTED')
    // 强制传入一个闭集外的字符串（TS 侧不可达，模拟脏数据）
    const leaked = indexErrorMessage('RuntimeError: query = TOP SECRET' as IndexErrorCode)
    expect(leaked).toBe(fallback)
    expect(leaked).not.toContain('TOP SECRET')
  })

  it('未知阶段走兜底标签', () => {
    const unknownStage = 'unknown' as never
    expect(indexStageLabel(null)).toBe(indexStageLabel(unknownStage))
    expect(indexStageLabel('garbage' as never)).toBe(indexStageLabel(unknownStage))
    expect(indexStageLabel('embed')).not.toBe(indexStageLabel(unknownStage))
  })

  it('DOCUMENT_NOT_FOUND 不可重试，其他闭集码可重试', () => {
    expect(canRetryIndexError('DOCUMENT_NOT_FOUND')).toBe(false)
    for (const code of BACKEND_CODES.filter(c => c !== 'DOCUMENT_NOT_FOUND')) {
      expect(canRetryIndexError(code as IndexErrorCode), code).toBe(true)
    }
    expect(canRetryIndexError(undefined)).toBe(true)
  })

  it('摘要只在有稳定码时输出，且包含阶段与可操作提示', () => {
    const empty: IndexPipeline = { stage: 'parse', progress: 0, error_code: null, error_detail: null }
    expect(indexPipelineSummary(empty)).toBe('')
    expect(indexPipelineSummary(undefined)).toBe('')
    const failed: IndexPipeline = {
      stage: 'embed',
      progress: 50,
      error_code: 'EMBED_FAILED',
      error_detail: 'stage=embed exception=RuntimeError'
    }
    const summary = indexPipelineSummary(failed)
    expect(summary).toContain(indexStageLabel('embed'))
    expect(summary).toContain('向量化服务不可用')
    expect(summary).not.toContain('RuntimeError')
  })
})
