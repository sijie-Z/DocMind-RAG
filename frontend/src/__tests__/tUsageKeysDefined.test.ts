/**
 * F 波（w2tf1）· t() 使用键扫描 —— 「使用侧键 ⊆ 词条定义键」的结构性护栏。
 *
 * 背景：i18n 缺口棘轮（completeness.test.ts）以 zh 为源对比四语键面，结构上抓不到
 * 「模板用了未定义键」——chat.exportChat 四语全缺、棘轮全绿（跨会话实机发现的棘轮盲区）。
 * 本用例补使用侧：扫描 src/**\/*.{vue,ts}（排除 locales/**、__tests__/**、*.test.ts、
 * *.spec.ts、*.d.ts）里的静态 t('key') / t("key") / $t('key') 调用，断言每个字面量键
 * 均可在 zh 中枢按点路径解析；动态键（模板串/变量/前缀拼接如 t('a.' + x)）跳过并计数。
 *
 * 存量治理（2026-10-15 F 波首扫，11 处）：chat.exportChat 已四语补齐；其余 10 处存量
 * 落入 tUsageKeys.baseline.json（棘轮：只缩不涨；收敛后 UPDATE_T_USAGE_BASELINE=1 重生成）。
 *
 * 口径：grep 级正则 + 注释剥离（与 hardcodedCjk.test.ts 同款：<!-- -->、块注释、// 行注释）；
 * 字面量判定 = 首参为整段字符串（闭引号后紧跟 , 或 )——前缀拼接计入动态键；
 * 解析规则与 completeness.test.ts 一致（逐步 in 判定）。
 *
 * 变异点：任一新增静态 t('未定义键') → 本用例红并点名 file#key；键被删/改名同红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import zh from '@/locales/zh'

const SRC = join(process.cwd(), 'src')
// vitest 的 cwd 恒为 frontend 根（npm script / npx vitest 均如此）
const BASELINE_FILE = join(SRC, '__tests__', 'tUsageKeys.baseline.json')
type Messages = Record<string, unknown>

/** 与 hardcodedCjk.test.ts 同款：注释剥离（Vue 模板注释、块注释、行注释含行尾） */
function stripComments(text: string, isVue: boolean): string {
  let t = isVue ? stripHtmlComments(text) : text
  t = t.replace(/\/\*[\s\S]*?\*\//g, '')
  t = t.replace(/\/\/.*$/gm, '')
  return t
}

/** 去除 HTML 注释：与 /<!--[\s\S]*?-->/g 等价的逐段扫描实现
 * （仅用于计数，非安全语境；逐段扫描同时避免正则式多字符剥离的静态告警） */
function stripHtmlComments(s: string): string {
  let out = ''
  let i = 0
  for (;;) {
    const a = s.indexOf('<!--', i)
    if (a < 0) return out + s.slice(i)
    const b = s.indexOf('-->', a + 4)
    if (b < 0) return out + s.slice(i)
    out += s.slice(i, a)
    i = b + 3
  }
}

interface Usage {
  file: string
  key: string
}

/** 扫描 src 下全部 .vue/.ts：收集静态键调用，并统计动态键调用数 */
function scanUsages(): { literal: Usage[]; dynamicCount: number; scannedFiles: number } {
  const literal: Usage[] = []
  const files: { rel: string; abs: string }[] = []
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir).sort()) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) {
        if (name === '__tests__' || name === 'node_modules') continue
        if (dir === SRC && name === 'locales') continue
        walk(p)
        continue
      }
      if (!/\.(vue|ts)$/.test(name)) continue
      if (name.endsWith('.d.ts') || /\.(test|spec)\.ts$/.test(name)) continue
      files.push({ rel: relative(SRC, p).replace(/\\/g, '/'), abs: p })
    }
  }
  walk(SRC)

  // 总调用面：t( / $t( / obj.t(（含字面量与动态）；字面量面：首参为整段字符串
  const callRe = /\b\$?t\s*\(/g
  const literalRe = /\b\$?t\s*\(\s*(['"])([^'"\n]+)\1\s*[,)]/g
  let dynamicCount = 0
  for (const f of files) {
    const text = stripComments(readFileSync(f.abs, 'utf-8'), f.rel.endsWith('.vue'))
    const total = Array.from(text.matchAll(callRe)).length
    let lits = 0
    for (const m of text.matchAll(literalRe)) {
      lits += 1
      literal.push({ file: f.rel, key: m[2] })
    }
    dynamicCount += total - lits
  }
  return { literal, dynamicCount, scannedFiles: files.length }
}

/** 与 completeness.test.ts 的 resolve 同款：zh 中枢点路径解析 */
function resolve(messages: Messages, path: string): unknown {
  let node: unknown = messages
  for (const seg of path.split('.')) {
    if (node && typeof node === 'object' && seg in (node as Messages)) {
      node = (node as Messages)[seg]
    } else {
      return undefined
    }
  }
  return node
}

const { literal, dynamicCount, scannedFiles } = scanUsages()
const undefinedUsages = literal.filter((u) => resolve(zh as Messages, u.key) === undefined)

interface Baseline {
  $comment: string
  toleratedUndefinedKeys: string[]
}

let baseline: Baseline = JSON.parse(readFileSync(BASELINE_FILE, 'utf-8'))

if (process.env.UPDATE_T_USAGE_BASELINE === '1') {
  const next: Baseline = {
    $comment: baseline.$comment,
    toleratedUndefinedKeys: Array.from(new Set(undefinedUsages.map((u) => u.key))).sort(),
  }
  writeFileSync(BASELINE_FILE, JSON.stringify(next, null, 2) + '\n', 'utf-8')
  process.stdout.write(`[t-usage] 基线已重写：容忍未定义键 ${next.toleratedUndefinedKeys.length} 个\n`)
  baseline = next
}

describe('F波 · t() 使用键 ⊆ zh 词条（使用侧扫描）', () => {
  it('静态 t() 键不新增未定义（基线之外清单为空）', () => {
    process.stdout.write(
      `[t-usage] 扫描 ${scannedFiles} 个文件；静态键 ${literal.length} 处；动态键 ${dynamicCount} 处（跳过并计数）；未定义 ${undefinedUsages.length} 处（基线 ${baseline.toleratedUndefinedKeys.length}）\n`,
    )
    const allowed = new Set(baseline.toleratedUndefinedKeys)
    const grown = undefinedUsages.filter((u) => !allowed.has(u.key))
    expect(
      grown.map((u) => `${u.file}#${u.key}`).join('\n'),
      `新增未定义键 ${grown.length} 个（使用侧键必须落在 zh 中枢，或显式重生成基线）`,
    ).toBe('')
    const shrank = baseline.toleratedUndefinedKeys.filter((k) => !undefinedUsages.some((u) => u.key === k))
    if (shrank.length > 0) {
      process.stdout.write(
        `[t-usage] ${shrank.length} 个基线键已可解析（收敛战果），可用 UPDATE_T_USAGE_BASELINE=1 重生成\n`,
      )
    }
  })
})
