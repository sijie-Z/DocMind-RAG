/**
 * issue #198 ② 判别测试：RRF 融合结果行的配色口径。
 *
 * 背景（分诊报告 #198，origin/develop 6db5fa61 复核）：
 *   - ① `stages` 空对象导致分节不渲染：**已修**（bf023d7d）——DebugPanel 恒渲染
 *     顶层 DebugStage，并把 `stages` 逐个子查询渲染；本文件只做回归（不得回退）。
 *   - ③ `chunkIndex` 永远 undefined：**已按真实响应修**（03210ae3）——类型只声明
 *     真实响应键；本文件不重复处理。
 *   - ② RRF 配色阈值失真：**仍在**——DebugSection 用 2.0/1.0/0.3 绝对分档，而
 *     RRF 分值域上限 `2/(k+1) ≈ 0.0328`（retriever._rrf_fuse），所有 RRF 行
 *     永远渲染成最低档灰色，误导融合质量判断。
 *
 * 修法（分诊推荐）：DebugSection 增加 `score-mode`——'absolute'（默认，
 * BM25/向量等绝对量纲）与 'rrf'（按列表名次着色：top3 绿 / 4-10 蓝 / 其余灰）；
 * DebugStage 的 RRF 节传 'rrf'，其余节行为逐字不变。
 *
 * 变异点（红→绿判别）：
 *   - RRF 节不传 score-mode / DebugSection 恢复绝对阈值 → 第 1 个用例红；
 *   - score-mode='rrf' 时仍按分值（而非名次）着色 → 第 1 个用例红；
 *   - 默认 absolute 模式被改坏（BM25 高分不再绿色）→ 第 2 个用例红；
 *   - 删掉顶层 DebugStage / stages 分节渲染（回退 ①）→ 第 3 个用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DebugPanel from '../components/DebugPanel.vue'
import DebugSection from '../components/DebugSection.vue'
import type { DebugHit, RetrievalDebugData } from '@/types/chat'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        rerankResultTitle: '重排结果',
        rerankPath: '路径: {path}',
        rerankStatus: { ok: '重排正常', degraded: '降级', disabled: '未启用', empty: '无候选' },
      },
    },
  },
})

function hit(index: number, score: number, filename = `doc-${index}.md`): DebugHit {
  return { id: `c${index}`, filename, score }
}

// 真实 RRF 取值域：k=60，双路命中上限 2/61 ≈ 0.0328。全部低于旧阈值 0.3，
// 修前一律灰色——这正是 issue #198 ② 的症状。
const RRF_SCORES = [
  0.0328, 0.0323, 0.0317, 0.0313, 0.0308, 0.0303,
  0.0299, 0.0294, 0.0290, 0.0286, 0.0282, 0.0278,
]

function mountSection(props: {
  title: string
  hits: DebugHit[]
  scoreLabel: string
  scoreKey?: 'score' | 'rank_score' | 'rerank_score'
  scoreMode?: 'absolute' | 'rrf'
}) {
  return mount(DebugSection, { props })
}

function scoreSpans(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('[data-testid="hit-score"]')
}

describe('issue #198 ② · RRF 行配色按名次而非不相干的绝对阈值', () => {
  it('RRF 分值域（≤0.033）下 top3 绿色、4-10 蓝色、其余灰色', () => {
    const wrapper = mountSection({
      title: '🔀 RRF 融合结果',
      hits: RRF_SCORES.map((s, i) => hit(i, s)),
      scoreLabel: 'score',
      scoreMode: 'rrf',
    })
    const spans = scoreSpans(wrapper)
    expect(spans.length).toBe(RRF_SCORES.length)

    // 修前：阈值 2.0/1.0/0.3 → 所有 RRF 行都是 text-gray-400（最低档）
    expect(spans[0].classes()).toContain('text-green-600')
    expect(spans[2].classes()).toContain('text-green-600')
    expect(spans[3].classes()).toContain('text-blue-600')
    expect(spans[9].classes()).toContain('text-blue-600')
    expect(spans[10].classes()).toContain('text-gray-400')
    expect(spans[0].classes()).not.toContain('text-gray-400')
  })

  it('默认 absolute 模式行为逐字不变（BM25/向量分档）', () => {
    const wrapper = mountSection({
      title: '🔤 BM25 关键词检索',
      hits: [hit(0, 2.5), hit(1, 1.2), hit(2, 0.5), hit(3, 0.1)],
      scoreLabel: 'score',
    })
    const spans = scoreSpans(wrapper)

    expect(spans[0].classes()).toContain('text-green-600')
    expect(spans[1].classes()).toContain('text-blue-600')
    expect(spans[2].classes()).toContain('text-yellow-600')
    expect(spans[3].classes()).toContain('text-gray-400')
  })

  it('DebugPanel 的 RRF 节使用 rrf 模式，且 stages 分节渲染不回退（①）', () => {
    const debugData: RetrievalDebugData = {
      stages: {
        什么是数据分级: {
          strategy: 'hybrid',
          query: '什么是数据分级',
          keyword_top: [hit(0, 5.2, 'bm25-best.md')],
          vector_top: [hit(1, 0.91, 'vector-best.md')],
          rrf_result: RRF_SCORES.map((s, i) => hit(i, s, `rrf-${i}.md`)),
        },
      },
    }
    const wrapper = mount(DebugPanel, {
      props: { debugData },
      global: { plugins: [i18n] },
    })

    // ① 回归：子查询 stage 的三段分节必须真实渲染（修前空对象分支 → 全空）
    const text = wrapper.text()
    expect(text).toContain('子查询: 什么是数据分级')
    expect(text).toContain('BM25 关键词检索')
    expect(text).toContain('向量相似度检索')
    expect(text).toContain('RRF 融合结果')

    const sections = wrapper.findAllComponents(DebugSection)
    const rrfSection = sections.find((s) => String(s.props('title')).includes('RRF'))
    const keywordSection = sections.find((s) => String(s.props('title')).includes('BM25'))
    expect(rrfSection, 'RRF 分节必须存在').toBeTruthy()
    expect(keywordSection, 'BM25 分节必须存在').toBeTruthy()

    // ② 接线：RRF 节显式使用 rrf 模式；其余节保持默认绝对分档
    expect(rrfSection!.props('scoreMode')).toBe('rrf')
    expect(keywordSection!.props('scoreMode')).toBeUndefined()

    // RRF 节内 top1 必须是最高档颜色（修前：全灰）
    const rrfSpans = rrfSection!.findAll('[data-testid="hit-score"]')
    expect(rrfSpans.length).toBe(RRF_SCORES.length)
    expect(rrfSpans[0].classes()).toContain('text-green-600')
  })
})
