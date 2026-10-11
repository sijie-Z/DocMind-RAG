/**
 * 工件（artifact）识别与下载工具 —— 体验第四波「工件渲染升级」的地基。
 *
 * 设计原则：纯函数、零依赖、可测。识别目标是"工具结果/运行报告这类文本更像
 * 什么"，宁可退回 text 也不误判——误判（比如把普通日志当 diff 染色）比不升级
 * 更伤体验，所以 diff 判定要求强特征（hunk 头 + 增删行），markdown 判定采用
 * 打分制（命中 ≥2 个特征才认）。
 */

export type ArtifactKind = 'diff' | 'json' | 'markdown' | 'text'

const HUNK_RE = /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/

/** 统一 diff 的强特征判定（git diff / patch 文本）。
 *  评审 F6：hunk 头必须**行首**（`@@` 不得先 trimStart）——上下文行形如
 *  ` @@ …` 的输入不应被当作 diff 证据。 */
export function isUnifiedDiff(text: string): boolean {
  if (!text) return false
  const lines = text.split('\n')
  const hasHunk = lines.some((l) => l.startsWith('@@') && HUNK_RE.test(l))
  const hasFileHeaders = lines.some((l) => /^---\s/.test(l)) && lines.some((l) => /^\+\+\+\s/.test(l))
  const addCount = lines.filter((l) => l.startsWith('+') && !l.startsWith('+++')).length
  const delCount = lines.filter((l) => l.startsWith('-') && !l.startsWith('---')).length
  if (hasHunk && (addCount + delCount) >= 1) return true
  // 被截断的文件式 diff（有 ---/+++ 头但没有完整 hunk）：要求像样的增删行
  if (hasFileHeaders && addCount >= 2 && delCount >= 1) return true
  return false
}

/** JSON 判定：整体可解析且是对象/数组（字符串/数字等标量不算工件）。 */
export function looksLikeJson(text: string): boolean {
  const t = text.trim()
  if (!t.startsWith('{') && !t.startsWith('[')) return false
  try {
    const value = JSON.parse(t)
    return typeof value === 'object' && value !== null
  } catch {
    return false
  }
}

/** Markdown 打分判定：命中 ≥2 个特征（标题/围栏/链接/列表/粗体/引用/表格）。 */
export function looksLikeMarkdown(text: string): boolean {
  let score = 0
  if (/^#{1,6}\s+\S/m.test(text)) score += 2
  if (/```/.test(text)) score += 2
  if (/^\s*[-*+]\s+\S/m.test(text)) score += 1
  if (/^\s*\d+\.\s+\S/m.test(text)) score += 1
  if (/\*\*[^*\n]+\*\*/.test(text)) score += 1
  if (/\[[^\]\n]+\]\((?:https?:|\/)[^)\n]+\)/.test(text)) score += 1
  if (/^\s*>/m.test(text)) score += 1
  if (/^\|.+\|/m.test(text)) score += 1
  return score >= 2
}

/** 识别工件类型（顺序即优先级：diff → json → markdown → text）。 */
export function detectArtifactKind(text: string): ArtifactKind {
  if (!text || !text.trim()) return 'text'
  if (isUnifiedDiff(text)) return 'diff'
  if (looksLikeJson(text)) return 'json'
  if (looksLikeMarkdown(text)) return 'markdown'
  return 'text'
}

// ── diff 行解析（供 DiffView 渲染差异视图）────────────────────────────────

export type DiffLineType = 'hunk' | 'add' | 'del' | 'ctx' | 'meta'

export interface DiffLine {
  type: DiffLineType
  text: string
  /** 旧文件行号（仅 hunk 内有效；其余为 null）。 */
  oldNo: number | null
  /** 新文件行号（仅 hunk 内有效；其余为 null）。 */
  newNo: number | null
}

/**
 * 解析统一 diff 为带行号的渲染行。行号维护：
 * - `@@ -a,b +c,d @@` 重置两侧游标；hunk 内 ctx 双增、add 只增新、del 只增旧；
 * - 首个 hunk 之前的行（diff --git / index / --- / +++ 等文件头）记为 meta；
 * - 没有 hunk 的退化输入（如被截断的 diff）按行首字符归 add/del/meta。
 *
 * 评审 F6 两处微边界修复：
 * - hunk 头判定要求**行首 `@@`**（不再 trimStart）——hunk 内形如 ` @@ …` 的
 *   上下文行归 ctx 正常计行号，不误当 hunk 头去重置游标；
 * - `--- `/`+++ ` 头仅在 **hunk 外** 归 meta——hunk 内增行内容以 `++ ` 开头
 *   （原文 `+++ …`）是真正的加行，不再被吞成 meta；`diff --git`/`index ` 出现
 *   即开启新的文件段（其后 `---`/`+++` 头恢复 meta 语义）。
 */
export function parseDiffLines(text: string): DiffLine[] {
  const out: DiffLine[] = []
  let oldNo = 0
  let newNo = 0
  let inHunk = false
  const rawLines = text.split('\n')
  // 文本以 \n 结尾时 split 产生尾部空元素——不是真实行，丢掉
  if (rawLines.length > 1 && rawLines[rawLines.length - 1] === '') rawLines.pop()
  for (const raw of rawLines) {
    if (raw.startsWith('@@') && HUNK_RE.test(raw)) {
      const m = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      if (m) {
        oldNo = parseInt(m[1], 10)
        newNo = parseInt(m[2], 10)
      }
      inHunk = true
      out.push({ type: 'hunk', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (raw.startsWith('diff --git ') || raw.startsWith('index ')) {
      // 新文件段开始：hunk 外语义复位（随后的 ---/+++ 头是文件头）
      inHunk = false
      out.push({ type: 'meta', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (!inHunk && (raw.startsWith('--- ') || raw.startsWith('+++ '))) {
      out.push({ type: 'meta', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (raw.startsWith('\\ No newline')) {
      out.push({ type: 'meta', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (!inHunk) {
      // 首个 hunk 前的内容不是 diff 正文（例如前置说明文字）
      out.push({ type: 'meta', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (raw.startsWith('+')) {
      out.push({ type: 'add', text: raw.slice(1), oldNo: null, newNo: newNo++ })
      continue
    }
    if (raw.startsWith('-')) {
      out.push({ type: 'del', text: raw.slice(1), oldNo: oldNo++, newNo: null })
      continue
    }
    // 上下文行（含 hunk 内的空行）
    const text_ = raw.startsWith(' ') ? raw.slice(1) : raw
    out.push({ type: 'ctx', text: text_, oldNo: oldNo++, newNo: newNo++ })
  }
  return out
}

// ── 下载 / 导出 ───────────────────────────────────────────────────────────

const EXT_BY_KIND: Record<ArtifactKind, string> = {
  diff: 'patch',
  json: 'json',
  markdown: 'md',
  text: 'txt',
}

/** 生成安全文件名：`{base}.{kind 对应扩展名}`。 */
export function artifactFilename(base: string, kind: ArtifactKind): string {
  const safe = (base || '')
    .replace(/[^\w.-]+/g, '_')
    .replace(/^[_.]+|[_.]+$/g, '')
    .slice(0, 60)
  return `${safe || 'artifact'}.${EXT_BY_KIND[kind]}`
}

/**
 * 下载文本为文件（Blob + 隐式 <a download>）。JSON 会先美化再落盘——
 * 让下载品比屏上截断预览更可读。URL.revokeObjectURL 延迟释放：
 * 部分浏览器在 click 后立即 revoke 会取消下载。
 */
export function downloadTextFile(filename: string, content: string, kind?: ArtifactKind): void {
  let payload = content
  if (kind === 'json') {
    try {
      payload = JSON.stringify(JSON.parse(content), null, 2)
    } catch {
      // 不可解析（如截断输入）：原样下载
    }
  }
  const blob = new Blob([payload], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
