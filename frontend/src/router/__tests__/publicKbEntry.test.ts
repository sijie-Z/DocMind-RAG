/**
 * 公开知识库入口的客户端导航边界（批次十八）判别用例。
 *
 * 变异点：
 * - 去掉 requiresAuth 判定 → 管理路由用例红；
 * - 去掉 Login/Register/FirstHome 显式拦截 → 登录界面用例红；
 * - 前缀放宽（无尾斜杠也视为公开入口）→ 与 nginx 边界不一致的用例红。
 */
import { describe, it, expect } from 'vitest'
import {
  isBlockedPublicKbEntryTarget,
  isPublicKbEntryDocument,
  isPublicKbEntryPath,
  normalizePathForNginxLocation,
  PUBLIC_KB_ENTRY_PREFIX,
} from '../publicKbEntry'

const NO_FLAGS = { requiresAuth: false, requiresAdmin: false, requiresPlatformAdmin: false }

describe('isPublicKbEntryPath', () => {
  it('与 nginx location /public/kb/ 前缀一致', () => {
    expect(PUBLIC_KB_ENTRY_PREFIX).toBe('/public/kb/')
    expect(isPublicKbEntryPath('/public/kb/tok-1')).toBe(true)
    expect(isPublicKbEntryPath('/public/kb/tok-1/child')).toBe(true)
    // nginx 的 `/public/kb`（无尾斜杠）落入 location /，不发 embed 头。
    expect(isPublicKbEntryPath('/public/kb')).toBe(false)
    expect(isPublicKbEntryPath('/public/kbx/tok')).toBe(false)
    expect(isPublicKbEntryPath('/dashboard')).toBe(false)
    expect(isPublicKbEntryPath('/')).toBe(false)
  })

  it('nginx 会规范化但浏览器 pathname 保留原样的写法仍识别为公开入口', () => {
    for (const pathname of [
      '//public/kb/tok-1',
      '/%70ublic/kb/tok-1',
      '/%2Fpublic/kb/tok-1',
      '/public/kb/tok-1/../tok-1',
      '/public/kb/.',
      '/public/kb/tok-1/..',
    ]) {
      expect(isPublicKbEntryPath(pathname), pathname).toBe(true)
    }
  })

  it('规范化后越出公开前缀的路径不得误判（含编码点段）', () => {
    for (const pathname of [
      '/public/kb/../admin',
      '/public/kb%2F..%2Fadmin',
      '/%2Fadmin',
      '/public/kb/..',
      '/public/kb',
      '/public/kbx/tok',
    ]) {
      expect(isPublicKbEntryPath(pathname), pathname).toBe(false)
    }
  })
})

describe('normalizePathForNginxLocation', () => {
  it('解码 + 合并斜杠 + 解析点段，并保留目录尾斜杠', () => {
    expect(normalizePathForNginxLocation('//public//kb/tok')).toBe('/public/kb/tok')
    expect(normalizePathForNginxLocation('/%70ublic/kb/tok')).toBe('/public/kb/tok')
    expect(normalizePathForNginxLocation('/public/kb/.')).toBe('/public/kb/')
    expect(normalizePathForNginxLocation('/public/kb/tok/..')).toBe('/public/kb/')
    expect(normalizePathForNginxLocation('/public/kb/../admin')).toBe('/public/admin')
    expect(normalizePathForNginxLocation('/')).toBe('/')
  })
})

describe('isPublicKbEntryDocument', () => {
  it('nginx 注入的服务器标记优先（路径再怪也认）', () => {
    expect(isPublicKbEntryDocument(true, '/dashboard')).toBe(true)
    expect(isPublicKbEntryDocument(true, '/')).toBe(true)
  })

  it('无标记时（如 vite dev）退回规范化路径判定', () => {
    expect(isPublicKbEntryDocument(false, '/public/kb/tok')).toBe(true)
    expect(isPublicKbEntryDocument(false, '//public/kb/tok')).toBe(true)
    expect(isPublicKbEntryDocument(false, '/dashboard')).toBe(false)
  })
})

describe('isBlockedPublicKbEntryTarget', () => {
  it('拦截所有鉴权/管理路由', () => {
    expect(isBlockedPublicKbEntryTarget('Dashboard', { ...NO_FLAGS, requiresAuth: true })).toBe(
      true,
    )
    expect(isBlockedPublicKbEntryTarget('Knowledge', { ...NO_FLAGS, requiresAdmin: true })).toBe(
      true,
    )
    expect(
      isBlockedPublicKbEntryTarget('McpServers', { ...NO_FLAGS, requiresPlatformAdmin: true }),
    ).toBe(true)
  })

  it('拦截登录/注册/营销首页（匿名但 UI 不属于公开知识库）', () => {
    for (const name of ['Login', 'Register', 'FirstHome']) {
      expect(isBlockedPublicKbEntryTarget(name, NO_FLAGS), name).toBe(true)
    }
  })

  it('公开内容与错误页不被拦截（避免公开页自身不可用）', () => {
    for (const name of ['PublicKb', 'NotFound', 'ServiceUnavailable', 'Privacy', 'Terms']) {
      expect(isBlockedPublicKbEntryTarget(name, NO_FLAGS), name).toBe(false)
    }
  })
})
