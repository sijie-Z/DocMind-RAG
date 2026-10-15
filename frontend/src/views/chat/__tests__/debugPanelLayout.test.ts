/**
 * B37-B40 契约回归：检索调试面板读点层级。
 *
 * 真值（后端装配）：
 *   - strategy 挂在**子查询 stage** 里（rag/retriever.py:696，
 *     debug_info 随 pipeline 的 stages[sq] 下发，pipeline.py:224）；
 *   - note 同样只在 stage 里（retriever.py:787 "RRF fusion empty, used fallback"）；
 *   - final_context 挂**顶层**（chat_service.py:305-313）；
 *   - 顶层 rerank_result 由 pipeline 合并所有子查询后写入（pipeline.py:262-272）。
 * 旧 UI：strategy/note 读顶层（恒空，B37/B38）；final_context/rerank_result 只挂在
 * 「无 stages」分支而 stages 恒真值 → 永不显示（B39/B40）。
 *
 * 变异点：把顶层 <DebugStage> 移除 / 把 strategy/note 块移回顶层读法 → 对应用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import DebugPanel from '../components/DebugPanel.vue'
import type { RetrievalDebugData } from '@/types/chat'

// 真实 hit 形状（retriever._debug_format_hits）与真实装配层级
const HIT = {
  id: 'c1',
  filename: 'architecture.md',
  score: 0.9123,
  keyword_rank: 1,
  vector_rank: 2,
  keyword_score: 0.5,
  vector_score: 0.8,
  rewrite_hits: 1,
  has_keyword: true,
  has_vector: true,
  snippet: '命中片段内容',
}

const LIVE_DEBUG: RetrievalDebugData = {
  elapsed_ms: 342.5,
  stages: {
    什么是架构: {
      strategy: 'hybrid',
      query: '什么是架构',
      keyword_top: [HIT],
      vector_top: [HIT],
      rrf_result: [HIT],
      note: 'RRF fusion empty, used fallback',
    },
  },
  rerank_result: [{ ...HIT, id: 'c2', filename: 'rerank-best.md', score: 1.5 }],
  total_results: 3,
  final_context: [{ rank: 1, filename: 'architecture.md', score: 0.9, content: '最终上下文片段ABC' }],
}

describe('DebugPanel/DebugStage · 读点层级对齐（B37-B40）', () => {
  it('strategy/note 从子查询 stage 渲染；final_context/rerank 从顶层渲染', () => {
    const wrapper = mount(DebugPanel, { props: { debugData: LIVE_DEBUG } })
    const text = wrapper.text()

    // B37/B38：strategy/note 真实位置在各 stage（旧读顶层 → 恒空）
    expect(text).toContain('hybrid')
    expect(text).toContain('RRF fusion empty, used fallback')

    // B39/B40：final_context / rerank_result 挂顶层（旧结构不可达 → 永不显示）
    expect(text).toContain('最终 LLM 上下文')
    expect(text).toContain('最终上下文片段ABC')
    expect(text).toContain('Cross-Encoder 重排结果')
    expect(text).toContain('rerank-best.md')

    // 子查询明细（keyword/vector/rrf 也在 stage 里）
    expect(text).toContain('子查询: 什么是架构')
    expect(text).toContain('BM25 关键词检索')
  })

  it('debugData 为空时不渲染面板主体', () => {
    const wrapper = mount(DebugPanel, { props: { debugData: null } })
    expect(wrapper.find('.border-t').exists()).toBe(false)
  })
})
