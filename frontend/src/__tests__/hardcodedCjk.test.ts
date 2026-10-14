/**
 * ISS-4 · 硬编码中文棘轮（src 全域，排除 locales 与测试；只防增长，不强制清零）。
 *
 * 背景：语言切到 en/ja/fr 后内容页中英混杂——根因是视图层大量硬编码中文。
 * 全量清理非本波范围，本用例先立"只缩不涨"的闸：
 *  - 口径：src 下 .vue/.ts（排除 locales/**、**​/__tests__/**、*.d.ts、
 *    *.test.ts/*.spec.ts），先剥离注释（<!-- -->、块注释、// 含行尾注释——
 *    中文注释是本仓风格，不计入），再统计"含 CJK 字符的行数"，per-file 记录；
 *  - 任一文件行数 > 基线 → 红（新增硬编码中文必须改为 i18n 词条，或确属合理
 *    例外时以 UPDATE_CJK_BASELINE=1 显式重生成基线，让增长在评审中可见）；
 *  - 新文件带 CJK 且不在基线 → 红；行数缩小 → 绿（可重生成基线锁住战果）。
 *
 * 有意改动后重生成：UPDATE_CJK_BASELINE=1 npx vitest run src/__tests__/hardcodedCjk.test.ts
 * 变异点：任一新硬编码中文行 / 基线文件删条目后增长 → 对应断言红并点名。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = join(process.cwd(), 'src')
const BASELINE_FILE = join(SRC, '__tests__', 'cjk-baseline.json')
const CJK = /[㐀-䶿一-鿿]/

/** 剥离注释：<!-- -->（Vue 模板）/ 块注释 / // 行注释（含行尾） */
function stripComments(text: string, isVue: boolean): string {
  let t = isVue ? text.replace(/<!--[\s\S]*?-->/g, '') : text
  t = t.replace(/\/\*[\s\S]*?\*\//g, '')
  t = t.replace(/\/\/.*$/gm, '')
  return t
}

/** 扫描 src：返回 { 相对路径: 含 CJK 行数 }，仅收录 > 0 的文件 */
function scan(): Record<string, number> {
  const out: Record<string, number> = {}
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir).sort()) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) {
        if (name === '__tests__' || name === 'node_modules') continue
        walk(p)
        continue
      }
      if (!/\.(vue|ts)$/.test(name)) continue
      if (name.endsWith('.d.ts') || /\.(test|spec)\.ts$/.test(name)) continue
      const rel = relative(SRC, p).replace(/\\/g, '/')
      if (rel.startsWith('locales/')) continue
      const text = stripComments(readFileSync(p, 'utf-8'), name.endsWith('.vue'))
      const n = text.split(/\r?\n/).filter((line) => CJK.test(line)).length
      if (n > 0) out[rel] = n
    }
  }
  walk(SRC)
  // 排序键，保证基线 JSON 稳定 diff
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => (a < b ? -1 : 1)))
}

interface Baseline {
  $comment: string
  files: Record<string, number>
}

const current = scan()
let baseline: Baseline = JSON.parse(readFileSync(BASELINE_FILE, 'utf-8'))

if (process.env.UPDATE_CJK_BASELINE === '1') {
  const next: Baseline = { $comment: baseline.$comment, files: current }
  writeFileSync(BASELINE_FILE, JSON.stringify(next, null, 2) + '\n', 'utf-8')
  baseline = next
  process.stdout.write(
    `[cjk-baseline] 已重写：${Object.keys(next.files).length} 个文件 / ${Object.values(next.files).reduce((a, b) => a + b, 0)} 行硬编码中文\n`,
  )
}

describe('ISS-4 · 硬编码中文棘轮（src/__tests__/hardcodedCjk.test.ts）', () => {
  it('基线非空（存量硬编码中文已记录在案）', () => {
    expect(Object.keys(baseline.files).length).toBeGreaterThan(0)
  })

  it('不存在基线之外的新硬编码中文文件', () => {
    const added = Object.keys(current).filter((f) => !(f in baseline.files))
    expect(added.join(', '), `新增带硬编码中文的文件 ${added.length} 个（请改用 i18n 词条；例外需重生成基线）`).toBe('')
  })

  it('存量文件的中文行数不得增长', () => {
    const grew: string[] = []
    for (const [f, n] of Object.entries(current)) {
      const base = baseline.files[f]
      if (base !== undefined && n > base) grew.push(`${f}（${base} → ${n}）`)
    }
    expect(grew.join(', '), `硬编码中文行数增长 ${grew.length} 处（改成 i18n 词条；例外需重生成基线）`).toBe('')
  })

  it('行数缩小 → 允许并提示收敛基线', () => {
    let shrunkFiles = 0
    let shrunkLines = 0
    for (const [f, base] of Object.entries(baseline.files)) {
      const n = current[f] ?? 0
      if (n < base) {
        shrunkFiles += 1
        shrunkLines += base - n
      }
    }
    if (shrunkFiles > 0) {
      process.stdout.write(
        `[cjk-baseline] 硬编码中文已缩小：${shrunkFiles} 个文件共 -${shrunkLines} 行，可用 UPDATE_CJK_BASELINE=1 更新基线锁住战果\n`,
      )
    }
    expect(true).toBe(true)
  })
})
