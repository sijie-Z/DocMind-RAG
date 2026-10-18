/**
 * i18n 键面快照护栏 —— zh 全量叶子键排序固化（i18n-key-snapshot.json）。
 *
 * 背景：completeness.test.ts 的 zh 闸只锁「叶子键数下限」，死键清扫/键改名在
 * 数量持平或可被 UPDATE_I18N_BASELINE=1 顺手改写时不留任何可见痕迹；本用例把
 * 当前 zh 全部叶子键排序固化为显式快照，任何键面变化（删除、改名、新增）都必须
 * 同时提交快照 diff，让「死键清扫」在评审中一眼可见、无法静默发生。
 *
 * 口径：叶子键展开与 completeness.test.ts 一致（对象递归、数组/标量视为叶子，
 * 全路径排序）。快照文件 src/locales/__tests__/i18n-key-snapshot.json。
 *
 * 有意改动键面（新增词条、死键清扫、键改名）后重生成：
 *   UPDATE_I18N_KEY_SNAPSHOT=1 npx vitest run src/locales/__tests__/keySnapshot.test.ts
 * 生成的快照必须随同 PR 提交——删除键不更新快照 → 本用例红并逐个点名被删键。
 *
 * 变异点：zh 任一叶子键被删/改名 → 红并点名；新增键未更新快照 → 红（新增同样
 * 计入键面，保证快照始终是全量真值，后续删除才可被追踪）。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import zh from '@/locales/zh'

type Messages = Record<string, unknown>

// vitest 的 cwd 恒为 frontend 根（npm script / npx vitest 均如此）
const SNAPSHOT_FILE = resolvePath(process.cwd(), 'src/locales/__tests__/i18n-key-snapshot.json')

/** 展开对象全路径（叶子键），口径与 completeness.test.ts 一致 */
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

interface Snapshot {
  $comment: string
  source: string
  keyCount: number
  keys: string[]
}

const currentKeys = paths(zh as Messages)
let snapshot: Snapshot = JSON.parse(readFileSync(SNAPSHOT_FILE, 'utf-8'))

if (process.env.UPDATE_I18N_KEY_SNAPSHOT === '1') {
  const next: Snapshot = {
    $comment: snapshot.$comment,
    source: 'zh',
    keyCount: currentKeys.length,
    keys: [...currentKeys],
  }
  writeFileSync(SNAPSHOT_FILE, JSON.stringify(next, null, 2) + '\n', 'utf-8')
  process.stdout.write(`[i18n-key-snapshot] 已重写：zh 叶子键 ${next.keyCount} 个\n`)
  // 写模式即用新快照继续断言（生成运行本身应全绿）
  snapshot = next
}

describe('i18n 键面快照（删除/改名/新增键必须显式留下快照 diff）', () => {
  it('快照非空（存量键面已固化在案）', () => {
    expect(snapshot.keys.length).toBeGreaterThan(0)
  })

  it('zh 键面不得删除/改名（被删键必须显式更新快照）', () => {
    const currentSet = new Set(currentKeys)
    const deleted = snapshot.keys.filter((k) => !currentSet.has(k))
    expect(
      deleted.join('\n'),
      `zh 键面被删除/改名 ${deleted.length} 个：死键清扫或改名必须同步提交 i18n-key-snapshot.json 的 diff` +
        `（UPDATE_I18N_KEY_SNAPSHOT=1 重生成），不得静默删除`,
    ).toBe('')
  })

  it('zh 键面新增必须同步快照（保证快照始终是全量真值）', () => {
    const snapshotSet = new Set(snapshot.keys)
    const added = currentKeys.filter((k) => !snapshotSet.has(k))
    expect(
      added.join('\n'),
      `zh 键面新增 ${added.length} 个但快照未更新：新增词条需同步重生成 i18n-key-snapshot.json` +
        `（UPDATE_I18N_KEY_SNAPSHOT=1）`,
    ).toBe('')
  })

  it('快照与 zh 键面逐项一致（含顺序），keyCount 元数据诚实', () => {
    expect(snapshot.keyCount).toBe(snapshot.keys.length)
    expect(currentKeys).toEqual(snapshot.keys)
  })
})
