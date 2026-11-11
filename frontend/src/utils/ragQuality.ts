/**
 * RAG 检索质量分档 —— Dashboard（admin / user）共用。
 *
 * 现场（2026-11-11）：档位只看命中率，`hitRate > 60` 判良好、`> 30` 判一般、其余判
 * 「需关注」。于是**只有 1 次检索且 0 命中**时，界面会给出「需关注 / 较差」的质量结论——
 * 把「样本不足」说成了「质量差」。Dashboard 的 RAG 卡现在已经有「无数据」态
 * （`totalQueries > 0`），但 1–4 次仍然会直接判档。
 *
 * 分档顺序：样本量 0 → no-data；1..(minSamples-1) → insufficient；之后才看命中率阈值。
 * 阈值沿用既有口径（>60 良好 / >30 一般 / 其余较差），不改语义。
 *
 * 变异：删掉 insufficient 分支（直接走阈值）→ `ragQuality.test.ts` 对应用例红。
 */
export type RagQualityLevel = 'no-data' | 'insufficient' | 'good' | 'fair' | 'poor'

/** 低于该样本量不下质量结论（n=1 的 0% 与 n=1000 的 0% 不是同一件事）。 */
export const RAG_QUALITY_MIN_SAMPLES = 5

export function classifyRagQuality(
  totalQueries: number,
  hitRate: number,
  minSamples: number = RAG_QUALITY_MIN_SAMPLES,
): RagQualityLevel {
  if (!Number.isFinite(totalQueries) || totalQueries <= 0) return 'no-data'
  if (!Number.isFinite(minSamples) || minSamples <= 0) return 'no-data'
  if (totalQueries < minSamples) return 'insufficient'
  if (!Number.isFinite(hitRate)) return 'no-data'
  if (hitRate > 60) return 'good'
  if (hitRate > 30) return 'fair'
  return 'poor'
}
