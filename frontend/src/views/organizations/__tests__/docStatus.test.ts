/**
 * 组织详情文档状态 tag 判别用例（UI 验收 round2 · B20 / A_sweep A1 实锤）。
 *
 * 真 UI 现场：抽屉「知识库」tab 的 architecture.md 状态 tag 原文 = 英文
 * 'indexed'，且色档按 `status === 'completed'` 判 → 黄色(g)误判。
 * 后端真源（organizations.py:429）原样吐裸枚举：pending/uploaded/parsing/
 * parsed/indexed/failed（注意 INDEXED 在此端口径是 'indexed'，与知识库列表
 * 的 'completed' 改写不同）。
 *
 * 变异点：删掉 'indexed' 键（还原漂移）→ 第 1 条用例精确红。
 */
import { describe, it, expect } from 'vitest'
import { orgDocStatusLabel, orgDocStatusType } from '../docStatus'

describe('orgDocStatus · 与 organizations 端点裸枚举对齐（B20）', () => {
  it('indexed（实锤现场值）→ success +「已索引」，不再直出英文、不再黄色误判', () => {
    expect(orgDocStatusLabel('indexed')).toBe('已索引')
    expect(orgDocStatusType('indexed')).toBe('success')
    expect(orgDocStatusLabel('indexed')).not.toBe('indexed')
    expect(orgDocStatusType('indexed')).not.toBe('warning')
  })

  it('六值域全对齐且无撒谎（pending/uploaded/parsing/parsed/indexed/failed）', () => {
    expect(orgDocStatusLabel('pending')).toBe('等待处理')
    expect(orgDocStatusLabel('uploaded')).toBe('已上传')
    expect(orgDocStatusLabel('parsing')).toBe('解析中')
    expect(orgDocStatusLabel('parsed')).toBe('已解析')
    expect(orgDocStatusLabel('failed')).toBe('失败')

    expect(orgDocStatusType('pending')).toBe('info')
    expect(orgDocStatusType('uploaded')).toBe('info')
    expect(orgDocStatusType('parsing')).toBe('warning')
    expect(orgDocStatusType('parsed')).toBe('warning')
    expect(orgDocStatusType('failed')).toBe('error')

    // 非终态绝不显示「已完成」/「已索引」
    expect(orgDocStatusLabel('parsing')).not.toBe('已索引')
    expect(orgDocStatusLabel('pending')).not.toBe('已完成')
  })

  it('兼容知识库端口径：completed → success +「已完成」', () => {
    expect(orgDocStatusLabel('completed')).toBe('已完成')
    expect(orgDocStatusType('completed')).toBe('success')
  })

  it('null/未知状态 → 「未知」+ default，不崩', () => {
    expect(orgDocStatusLabel(null)).toBe('未知')
    expect(orgDocStatusLabel(undefined)).toBe('未知')
    expect(orgDocStatusType(null)).toBe('default')
    expect(orgDocStatusType('whatever')).toBe('default')
  })
})
