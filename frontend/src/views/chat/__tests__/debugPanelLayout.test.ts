/**
 * B37-B40 契约回归：检索调试面板读点层级 + issue #189 重排状态可见性。
 *
 * 真值（后端装配）：
 *   - strategy 挂在**子查询 stage** 里（rag/retriever.py:696，
 *     debug_info 随 pipeline 的 stages[sq] 下发，pipeline.py:224）；
 *   - note 同样只在 stage 里（retriever.py:787 "RRF fusion empty, used fallback"）；
 *   - final_context 挂**顶层**（chat_service.py:305-313）；
 *   - 顶层 rerank_result / rerank（status/path/reason，issue #189）由 pipeline
 *     合并所有子查询后写入（pipeline.py:305-330）。
 * 旧 UI：strategy/note 读顶层（恒空，B37/B38）；final_context/rerank_result 只挂在
 * 「无 stages」分支而 stages 恒真值 → 永不显示（B39/B40）。
 *
 * 变异点：把顶层 <DebugStage> 移除 / 把 strategy/note 块移回顶层读法 /
 * 隐藏 rerank 状态徽标（#189 降级信号不可达 UI）→ 对应用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DebugPanel from '../components/DebugPanel.vue'
import type { RetrievalDebugData } from '@/types/chat'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        rerankResultTitle: '重排结果',
        rerankPath: '路径: {path}',
        rerankStatus: {
          ok: '重排正常',
          degraded: '重排已降级（原始顺序）',
          disabled: '重排未启用',
          empty: '无候选可重排',
        },
      },
    },
  },
})

// 真实 hit 形状（retriever._debug_format_hits / pipeline rerank_result）与真实装配层级
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
  // issue #190：rerank_result 携带重排绝对分（rerank_score），前端展示它而非旧排序分
  rerank_result: [{ ...HIT, id: 'c2', filename: 'rerank-best.md', score: 0.95, rank_score: 0.95, rerank_score: 8.42 }],
  // issue #189：重排路径状态由 pipeline 顶层写入
  rerank: {
    status: 'ok', path: 'local', reason: null,
    attempts: [{ path: 'local', ok: true, reason: null }],
    scores_attached: true, model: 'BAAI/bge-reranker-base',
  },
  total_results: 3,
  final_context: [{ rank: 1, filename: 'architecture.md', score: 0.9, content: '最终上下文片段ABC' }],
}

function mountPanel(debugData: RetrievalDebugData | null) {
  return mount(DebugPanel, {
    props: { debugData },
    global: { plugins: [i18n] },
  })
}

describe('DebugPanel/DebugStage · 读点层级对齐（B37-B40）', () => {
  it('strategy/note 从子查询 stage 渲染；final_context/rerank 从顶层渲染', () => {
    const wrapper = mountPanel(LIVE_DEBUG)
    const text = wrapper.text()

    // B37/B38：strategy/note 真实位置在各 stage（旧读顶层 → 恒空）
    expect(text).toContain('hybrid')
    expect(text).toContain('RRF fusion empty, used fallback')

    // B39/B40：final_context / rerank_result 挂顶层（旧结构不可达 → 永不显示）
    expect(text).toContain('最终 LLM 上下文')
    expect(text).toContain('最终上下文片段ABC')
    expect(text).toContain('重排结果')
    expect(text).toContain('rerank-best.md')

    // issue #189/#190：路径状态与重排绝对分可见
    expect(text).toContain('重排正常')
    expect(text).toContain('路径: local')
    expect(text).toContain('rerank: 8.42')

    // 子查询明细（keyword/vector/rrf 也在 stage 里）
    expect(text).toContain('子查询: 什么是架构')
    expect(text).toContain('BM25 关键词检索')
  })

  it('rerank degraded 状态在 UI 可见（#189：降级信号不得静默）', () => {
    const degraded: RetrievalDebugData = {
      ...LIVE_DEBUG,
      rerank_result: [{ ...HIT, id: 'c2', filename: 'rerank-best.md', score: 0.95, rank_score: 0.95, rerank_score: null }],
      rerank: {
        status: 'degraded', path: 'noop', reason: 'all_rerank_paths_failed',
        attempts: [
          { path: 'local', ok: false, reason: 'local_model_unavailable' },
          { path: 'zhipu', ok: false, reason: 'no_api_key' },
          { path: 'llm', ok: false, reason: 'no_llm_client' },
        ],
        scores_attached: false, model: null,
      },
    }
    const text = mountPanel(degraded).text()

    expect(text).toContain('重排已降级（原始顺序）')
    expect(text).toContain('路径: noop')
    expect(text).toContain('all_rerank_paths_failed')
    // 无 rerank_score 时回退展示排序分，而不是渲染 null
    expect(text).toContain('rerank: 0.95')
  })

  it('debugData 为空时不渲染面板主体', () => {
    const wrapper = mountPanel(null)
    expect(wrapper.find('.border-t').exists()).toBe(false)
  })
})
