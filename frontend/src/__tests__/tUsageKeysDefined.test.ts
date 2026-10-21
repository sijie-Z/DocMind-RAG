/**
 * F 波（w2tf1）· t() 使用键扫描 —— 「使用侧键 ⊆ 词条定义键」的结构性护栏。
 *
 * 背景：i18n 缺口棘轮（completeness.test.ts）以 zh 为源对比四语键面，结构上抓不到
 * 「模板用了未定义键」——chat.exportChat 四语全缺、棘轮全绿（跨会话实机发现的棘轮盲区）。
 * 本用例补使用侧：扫描 src/**\/*.{vue,ts}（排除 locales/**、__tests__/**、*.test.ts、
 * *.spec.ts、*.d.ts）里的静态 t('key') / t("key") / $t('key') 调用，断言每个字面量键
 * 均可在 zh 中枢按点路径解析；动态键（模板串/变量/前缀拼接如 t('a.' + x)）跳过并计数。
 *
 * 动态前缀白名单（tUsageDynamicPrefixes.json）：反引号模板 t(`prefix.${x}`) 与前缀
 * 拼接 t('prefix.' + x) 虽无法静态解析全键，但「${ / + 之前的静态前缀段」是可知的。
 * 该前缀必须显式登记在白名单中（未登记即红，点名 file#prefix）；登记项还必须指向
 * zh 中枢存在的对象节点（域名改名/删域后白名单失效即红）。登记即评审可见的承诺：
 * 该前缀域下的动态键由定义侧（completeness 棘轮）兜底。前缀为空的完全动态键
 * （如 t(`${x}`)）无静态段可护，维持跳过计数。
 *
 * 存量治理（2026-10-15 F 波首扫，11 处）：chat.exportChat 已四语补齐；其余 10 处存量
 * 落入 tUsageKeys.baseline.json（棘轮：只缩不涨；收敛后 UPDATE_T_USAGE_BASELINE=1 重生成）。
 *
 * 口径：grep 级正则 + 注释剥离（与 hardcodedCjk.test.ts 同款：<!-- -->、块注释、// 行注释）；
 * 字面量判定 = 首参为整段字符串（闭引号后紧跟 , 或 )，或无插值的反引号模板串——
 * 前缀拼接计入动态键；解析规则与 completeness.test.ts 一致（逐步 in 判定）。
 *
 * 变异点：任一新增静态 t('未定义键') → 本用例红并点名 file#key；键被删/改名同红；
 * 任一未登记前缀的动态 t() → 红并点名 file#prefix；白名单前缀不再指向 zh 对象节点 → 红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import zh from '@/locales/zh'

const SRC = join(process.cwd(), 'src')
// vitest 的 cwd 恒为 frontend 根（npm script / npx vitest 均如此）
const BASELINE_FILE = join(SRC, '__tests__', 'tUsageKeys.baseline.json')
const PREFIX_FILE = join(SRC, '__tests__', 'tUsageDynamicPrefixes.json')
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

/** 动态键的前缀段：t(`prefix.${x}`) / t('prefix.' + x) 中 ${ / + 之前的静态部分 */
interface DynamicUsage {
  file: string
  prefix: string
  form: 'template' | 'concat'
}

/** 扫描 src 下全部 .vue/.ts：收集静态键调用与动态前缀，并统计动态键调用数 */
function scanUsages(): {
  literal: Usage[]
  dynamic: DynamicUsage[]
  dynamicCount: number
  scannedFiles: number
} {
  const literal: Usage[] = []
  const dynamic: DynamicUsage[] = []
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
  // 反引号模板：t(`prefix.${x}`)；无 ${ 插值的模板串等价静态键
  const templateRe = /\b\$?t\s*\(\s*`([^`]*)`/g
  // 前缀拼接：t('prefix.' + x)——首参为字符串字面量后接 + 的动态形态
  const concatRe = /\b\$?t\s*\(\s*(['"])([^'"\n]*)\1\s*\+/g
  let dynamicCount = 0
  for (const f of files) {
    const text = stripComments(readFileSync(f.abs, 'utf-8'), f.rel.endsWith('.vue'))
    const total = Array.from(text.matchAll(callRe)).length
    let lits = 0
    for (const m of text.matchAll(literalRe)) {
      lits += 1
      literal.push({ file: f.rel, key: m[2] })
    }
    for (const m of text.matchAll(templateRe)) {
      const body = m[1]
      const at = body.indexOf('${')
      if (at < 0) {
        lits += 1
        literal.push({ file: f.rel, key: body })
      } else if (at > 0) {
        dynamic.push({ file: f.rel, prefix: body.slice(0, at), form: 'template' })
      }
      // at === 0：完全动态键（无静态前缀段），无前缀可护，计入动态计数
    }
    for (const m of text.matchAll(concatRe)) {
      if (m[2].length > 0) dynamic.push({ file: f.rel, prefix: m[2], form: 'concat' })
    }
    dynamicCount += total - lits
  }
  return { literal, dynamic, dynamicCount, scannedFiles: files.length }
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

const { literal, dynamic, dynamicCount, scannedFiles } = scanUsages()
const undefinedUsages = literal.filter((u) => resolve(zh as Messages, u.key) === undefined)

interface Baseline {
  $comment: string
  toleratedUndefinedKeys: string[]
}

interface PrefixWhitelist {
  $comment: string
  prefixes: string[]
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
      `[t-usage] 扫描 ${scannedFiles} 个文件；静态键 ${literal.length} 处；动态键 ${dynamicCount} 处（其中可护前缀 ${dynamic.length} 处）；未定义 ${undefinedUsages.length} 处（基线 ${baseline.toleratedUndefinedKeys.length}）\n`,
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

  it('动态键前缀必须已在白名单声明（未声明前缀即红）', () => {
    const whitelist: PrefixWhitelist = JSON.parse(readFileSync(PREFIX_FILE, 'utf-8'))
    const declared = new Set(whitelist.prefixes)
    const formName: Record<DynamicUsage['form'], string> = { template: '模板串', concat: '前缀拼接' }
    const undeclared = dynamic.filter((d) => !declared.has(d.prefix))
    expect(
      undeclared.map((d) => `${d.file}#${d.prefix}（${formName[d.form]}）`).join('\n'),
      `未声明动态前缀 ${undeclared.length} 个：t() 动态键的前缀段必须在 src/__tests__/tUsageDynamicPrefixes.json 显式登记` +
        `（新形态请在 PR 中一并声明）`,
    ).toBe('')
    const used = new Set(dynamic.map((d) => d.prefix))
    const stale = whitelist.prefixes.filter((p) => !used.has(p))
    if (stale.length > 0) {
      process.stdout.write(
        `[t-usage] ${stale.length} 个白名单前缀当前无调用引用（可清理）：${stale.join(', ')}\n`,
      )
    }
  })

  it('白名单前缀必须指向 zh 中枢存在的对象节点（防域名失效）', () => {
    const whitelist: PrefixWhitelist = JSON.parse(readFileSync(PREFIX_FILE, 'utf-8'))
    const bad: string[] = []
    for (const p of whitelist.prefixes) {
      const node = resolve(zh as Messages, p.replace(/\.$/, ''))
      if (!node || typeof node !== 'object' || Array.isArray(node)) bad.push(p)
    }
    expect(
      bad.join(', '),
      `白名单前缀 ${bad.length} 个未指向 zh 对象节点（域名被改名/删除后白名单失效，动态键将回显裸键）`,
    ).toBe('')
  })
})
