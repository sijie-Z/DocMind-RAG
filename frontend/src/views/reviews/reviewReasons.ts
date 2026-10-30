/**
 * 核验原因码 → 业务文案（用户可读）。
 *
 * 后端原因码来源：
 * - 文档 readiness：parse_failed / not_indexed / no_chunks / no_original_text /
 *   legacy_unverified / missing_version_meta / wrong_org；
 * - 建案跨文档约束：family_mismatch / version_order；side 前缀（policy|base|revised:）；
 * - 导出阻断：undecided / not_analyzed；
 * - 事实歧义：unit_unclear / multiple_durations（可与 '+' 组合）。
 *
 * 口径：未知码原样透传（绝不吞掉信息）；列表/详情/建案弹窗/导出 409 共用本模块。
 * 键面使用静态字面量映射（tUsageKeysDefined 门禁要求前缀是对象节点）。
 */
type TranslateFn = (key: string) => string

const REASON_KEYS: Record<string, string> = {
  parse_failed: 'reviews.reasonParseFailed',
  not_indexed: 'reviews.reasonNotIndexed',
  no_chunks: 'reviews.reasonNoChunks',
  no_original_text: 'reviews.reasonNoOriginalText',
  legacy_unverified: 'reviews.reasonLegacyUnverified',
  missing_version_meta: 'reviews.reasonMissingVersionMeta',
  wrong_org: 'reviews.reasonWrongOrg',
  wrong_role: 'reviews.reasonWrongRole',
  family_mismatch: 'reviews.reasonFamilyMismatch',
  version_order: 'reviews.reasonVersionOrder',
  undecided: 'reviews.reasonUndecided',
  not_analyzed: 'reviews.reasonNotAnalyzed',
  unit_unclear: 'reviews.ambiguousUnit',
  multiple_durations: 'reviews.ambiguousMultiple'
}

const SIDE_KEYS: Record<string, string> = {
  policy: 'reviews.docPolicy',
  base: 'reviews.docBase',
  revised: 'reviews.docRevised'
}

function singleLabel(t: TranslateFn, code: string): string {
  const key = REASON_KEYS[code]
  return key ? t(key) : code
}

/** 单条原因码（可含 side: 前缀或 '+' 组合码）→ 本地化文案。 */
export function reasonLabel(t: TranslateFn, code: string): string {
  const parts = String(code)
    .split('+')
    .map((part) => part.trim())
    .filter(Boolean)
  if (parts.length === 0) return String(code)
  return parts
    .map((part) => {
      const colon = part.indexOf(':')
      if (colon > 0) {
        const side = part.slice(0, colon)
        const rest = part.slice(colon + 1)
        const sideKey = SIDE_KEYS[side]
        if (sideKey) return `${t(sideKey)}：${singleLabel(t, rest)}`
      }
      return singleLabel(t, part)
    })
    .join(' · ')
}

/** 原因码列表 → 本地化文案（保持顺序）。 */
export function reasonsLabel(t: TranslateFn, codes: ReadonlyArray<string> | null | undefined): string {
  return (codes ?? []).map((code) => reasonLabel(t, String(code))).join(' · ')
}
