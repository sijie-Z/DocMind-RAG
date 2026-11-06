import type { KnowledgeSource } from '@/types/chat'

/** 正文引用使用后端原始 rank；仅旧数据缺 rank 时回退到过滤后数组位置。 */
export function citationRank(source: Pick<KnowledgeSource, 'rank'>, index: number): number {
  return source.rank ?? index + 1
}

/**
 * 正文 [n] → 来源对象。
 *
 * 后端终态/落库的 sources 只保留「被引用项」但保留原始 rank（chat_service
 * build_sources_list 之后按 cited_indices 过滤）。因此必须优先按 rank 查找，
 * 不能用过滤后数组下标；只有全部来源都缺 rank（旧历史）才回退位置。
 */
export function findSourceByCitation(
  sources: KnowledgeSource[],
  index: number
): KnowledgeSource | undefined {
  if (!sources.length || !Number.isInteger(index) || index < 1) return undefined
  if (sources.some((source) => typeof source.rank === 'number')) {
    return sources.find((source, position) => citationRank(source, position) === index)
  }
  return sources[index - 1]
}
