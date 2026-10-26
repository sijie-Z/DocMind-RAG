import type { KnowledgeSource } from '@/types/chat'

/** 正文引用使用后端原始 rank；仅旧数据缺 rank 时回退到过滤后数组位置。 */
export function citationRank(source: Pick<KnowledgeSource, 'rank'>, index: number): number {
  return source.rank ?? index + 1
}
