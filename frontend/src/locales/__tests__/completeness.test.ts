/**
 * ISS-4 · i18n 缺口棘轮（全量叶子键面 × en/ja/fr，只缩不涨）。
 *
 * 背景：zh 为基准（各语键面按 zh 全路径展开）；ja/fr 存在成片缺口（主要在
 * chat 等域），en 曾缺个位数。本用例把"当前缺口清单"固化为
 * i18n-baseline.json，将缺口变成**棘轮**：
 *  - 任一语出现基线之外的新缺口 → 红，并逐个点名（新键漏翻 / 键被删漏补均命中）；
 *  - 缺口缩小 → 绿（可选地以 UPDATE_I18N_BASELINE=1 收敛基线，锁住战果）；
 *  - en 为硬闸：必须 0 缺口（locales/index.ts 的 fallbackLocale='en'
 *    以 en 完备为前提——见 index.ts 注释）。
 *
 * 有意改动基线（新增词条、批量补译后）：
 *   UPDATE_I18N_BASELINE=1 npx vitest run src/locales/__tests__/completeness.test.ts
 * 生成器会重写 i18n-baseline.json（缺键清单按 zh 基准序），随同 PR 提交。
 *
 * 变异点：任一语删键/漏键 → 对应断言红并点名；en 任一键缺失 → en 硬闸红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

type Messages = Record<string, unknown>

// vitest 的 cwd 恒为 frontend 根（npm script / npx vitest 均如此）
const BASELINE_FILE = resolvePath(process.cwd(), 'src/locales/__tests__/i18n-baseline.json')
type Target = 'en' | 'ja' | 'fr'

const MESSAGES: Record<Target, Messages> = {
  en: en as Messages,
  ja: ja as Messages,
  fr: fr as Messages,
}

/** 展开对象全路径（叶子键），口径与 governanceI18nKeys/contentI18nKeys 先例一致 */
function paths(o: Messages, pre = ''): string[] {
  let out: string[] = []
  for (const k of Object.keys(o)) {
    const p = pre ? `${pre}.${k}` : k
    const v = o[k]
    if (v && typeof v === 'object' && !Array.isArray(v)) out = out.concat(paths(v as Messages, p))
    else out.push(p)
  }
  return out.sort()
}

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

/** 缺键判定口径与各命名空间用例一致：非空字符串且不等于键名本身 */
function missingKeys(base: string[], messages: Messages): string[] {
  const missing: string[] = []
  for (const p of base) {
    const v = resolve(messages, p)
    if (typeof v !== 'string' || v.length === 0 || v === p) missing.push(p)
  }
  return missing
}

const zhPaths = paths(zh as Messages)
const currentMissing: Record<Target, string[]> = {
  en: missingKeys(zhPaths, MESSAGES.en),
  ja: missingKeys(zhPaths, MESSAGES.ja),
  fr: missingKeys(zhPaths, MESSAGES.fr),
}

interface Baseline {
  $comment: string
  source: string
  zhLeafKeys: number
  locales: Record<Target, string[]>
}

let baseline: Baseline = JSON.parse(readFileSync(BASELINE_FILE, 'utf-8'))

if (process.env.UPDATE_I18N_BASELINE === '1') {
  const next: Baseline = {
    $comment: baseline.$comment,
    source: 'zh',
    zhLeafKeys: zhPaths.length,
    locales: {
      en: [...currentMissing.en],
      ja: [...currentMissing.ja],
      fr: [...currentMissing.fr],
    },
  }
  writeFileSync(BASELINE_FILE, JSON.stringify(next, null, 2) + '\n', 'utf-8')
  process.stdout.write(
    `[i18n-baseline] 已重写：zh=${next.zhLeafKeys} 键；缺口 en=${next.locales.en.length} ja=${next.locales.ja.length} fr=${next.locales.fr.length}\n`,
  )
  // 写模式即用新基线继续断言（生成运行本身应全绿）
  baseline = next
}

describe('ISS-4 · i18n 缺口棘轮（locales/__tests__/completeness.test.ts）', () => {
  it('zh 基准键面非空，且不得静默缩水（删除键需重生成基线）', () => {
    expect(zhPaths.length, `zh 叶子键 ${zhPaths.length} < 基线 ${baseline.zhLeafKeys}`).toBeGreaterThanOrEqual(
      baseline.zhLeafKeys,
    )
  })

  it('en 缺口必须为 0（fallbackLocale=en 的前提）', () => {
    expect(currentMissing.en.join(', '), `en 缺失键（${currentMissing.en.length}）`).toBe('')
  })

  for (const lang of ['ja', 'fr'] as const) {
    it(`${lang}: 不得新增缺口（相对基线只缩不涨）`, () => {
      const allowed = new Set(baseline.locales[lang])
      const grew = currentMissing[lang].filter((k) => !allowed.has(k))
      expect(
        grew.join(', '),
        `${lang} 新增缺口 ${grew.length} 个（基线 ${baseline.locales[lang].length} → 现状 ${currentMissing[lang].length}）`,
      ).toBe('')
      const shrank = baseline.locales[lang].filter((k) => !currentMissing[lang].includes(k))
      if (shrank.length > 0) {
        process.stdout.write(
          `[i18n-baseline] ${lang} 缺口已缩小 ${shrank.length} 个，可用 UPDATE_I18N_BASELINE=1 更新基线锁住战果\n`,
        )
      }
    })
  }
})
