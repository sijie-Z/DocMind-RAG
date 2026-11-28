/**
 * PWA 导航回退排除（批次十八）判别用例。
 *
 * 公开知识库的 embed 响应头只由 nginx 的 /public/kb/ location 下发；
 * Service Worker 的 navigateFallback 会用预缓存 index.html 顶替导航，
 * 该响应带 SAMEORIGIN，导致 iframe 被拒。因此必须把公开知识库排除在
 * 导航回退之外，且 vite.config.mts 必须真的引用这份清单。
 */
import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { PUBLIC_KB_NAVIGATE_DENYLIST } from '../pwa'

/** 兼容从 frontend/ 或仓库根运行 vitest（不依赖 import.meta.url 的 scheme）。 */
function findFrontendRoot(): string {
  const cwd = process.cwd()
  if (existsSync(resolve(cwd, 'vite.config.mts'))) return cwd
  const nested = resolve(cwd, 'frontend')
  if (existsSync(resolve(nested, 'vite.config.mts'))) return nested
  throw new Error('无法定位 frontend/vite.config.mts')
}

const FRONTEND_ROOT = findFrontendRoot()
const VITE_CONFIG = resolve(FRONTEND_ROOT, 'vite.config.mts')

function denied(path: string): boolean {
  return PUBLIC_KB_NAVIGATE_DENYLIST.some((pattern) => pattern.test(path))
}

/** 去掉注释再做配置断言：注释掉的接线不算生效。 */
function stripComments(text: string): string {
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/#.*$/, '').replace(/\/\/.*$/, ''))
    .join('\n')
}

describe('PUBLIC_KB_NAVIGATE_DENYLIST', () => {
  it('公开知识库导航绕过 SW 回退', () => {
    expect(denied('/public/kb/tok-1')).toBe(true)
    expect(denied('/public/kb/tok-1?embed=1')).toBe(true)
    // nginx 会合并重复斜杠；同一路径不得因 SW 回退拿到 SAMEORIGIN 缓存壳。
    expect(denied('//public/kb/tok-1')).toBe(true)
  })

  it('普通/管理路由仍走既有回退（不扩大影响面）', () => {
    expect(denied('/')).toBe(false)
    expect(denied('/dashboard')).toBe(false)
    expect(denied('/login')).toBe(false)
    expect(denied('/public/kbx/tok')).toBe(false)
  })

  it('vite.config.mts 真的使用了这份排除清单（防清单被闲置/注释掉）', () => {
    const config = stripComments(readFileSync(VITE_CONFIG, 'utf8'))
    expect(config).toMatch(
      /navigateFallbackDenylist:\s*PUBLIC_KB_NAVIGATE_DENYLIST\s*,/,
    )
  })
})
