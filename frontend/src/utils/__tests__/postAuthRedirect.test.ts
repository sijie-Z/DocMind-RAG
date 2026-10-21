/**
 * 认证后落点解析（登录/注册共用）判别测试 —— ISS-1。
 *
 * 背景：注册成功曾硬跳 '/'（营销页），新用户拿到 token 却被丢回主页面无登录态；
 * 且登录侧 redirect 校验只有「以 / 开头 + 非 // 开头」的浅校验，下列变体可绕过，
 * 构成开放重定向风险（/\evil.com 会被浏览器归一化为 //evil.com 等）。
 *
 * 修法：抽出 resolvePostAuthTarget —— 合法站内路径优先，其余一律回落可信默认
 * 落点（/dashboard）；注册/登录两侧共用。
 *
 * 变异点：任一恶意变体被放行 / 默认落点被改动 → 对应用例精确红。
 */
import { describe, it, expect } from 'vitest'
import {
  DEFAULT_POST_AUTH_TARGET,
  resolvePostAuthTarget,
  sanitizeInternalRedirect,
} from '@/utils/postAuthRedirect'

describe('postAuthRedirect · 恶意 redirect 变体全拒', () => {
  const malicious: Array<[string, string]> = [
    ['协议相对 //evil.com', '//evil.com'],
    ['仅双斜杠 //', '//'],
    ['三斜杠 ///evil.com', '///evil.com'],
    ['协议相对 + 路径 //evil.com/a/b', '//evil.com/a/b'],
    ['反斜杠变体 /\\evil.com（浏览器归一化为 //）', '/\\evil.com'],
    ['双反斜杠 /\\\\evil.com', '/\\\\evil.com'],
    ['路径内嵌反斜杠 /a\\b', '/a\\b'],
    ['反斜杠开头 \\evil.com', '\\evil.com'],
    ['绝对 URL https://evil.com', 'https://evil.com'],
    ['绝对 URL http://evil.com', 'http://evil.com'],
    ['伪协议 javascript:alert(1)', 'javascript:alert(1)'],
    ['伪协议 data:text/html', 'data:text/html,<script>alert(1)</script>'],
    ['相对路径 evil.com（非 / 开头）', 'evil.com'],
    ['相对路径 dashboard（非 / 开头）', 'dashboard'],
    ['编码斜杠开头（非 / 开头）', '%2f%2fevil.com'],
    ['编码制表符 /%09', '/%09'],
    ['编码制表符 + 路径 /%09/evil.com', '/%09/evil.com'],
    ['编码制表符 + 域名 /%09evil.com', '/%09evil.com'],
    ['双重编码制表符 /%2509evil.com', '/%2509evil.com'],
    ['编码反斜杠 /%5cevil.com', '/%5cevil.com'],
    ['编码双反斜杠 /%5c%5cevil.com', '/%5c%5cevil.com'],
    ['编码双斜杠 /%2f%2fevil.com', '/%2f%2fevil.com'],
    ['编码双斜杠（大写十六进制）/ %2F%2Fevil.com', '/%2F%2Fevil.com'],
    ['原始制表符 /（TAB）/dashboard', '/\t/dashboard'],
    ['原始换行 /dash（LF）board', '/dash\nboard'],
    ['原始回车 /dash（CR）', '/dash\r'],
    ['原始 NUL /（U+0000）', '/\u0000'],
    ['空串', ''],
    ['纯空格', '   '],
  ]

  for (const [name, evil] of malicious) {
    it(`拒绝并回落默认落点：${name}`, () => {
      expect(sanitizeInternalRedirect(evil)).toBeNull()
      expect(resolvePostAuthTarget(evil)).toBe(DEFAULT_POST_AUTH_TARGET)
    })
  }

  it('非字符串类型一律回落默认落点', () => {
    for (const raw of [undefined, null, 123, {}, ['/dashboard'], true]) {
      expect(sanitizeInternalRedirect(raw)).toBeNull()
      expect(resolvePostAuthTarget(raw)).toBe(DEFAULT_POST_AUTH_TARGET)
    }
  })
})

describe('postAuthRedirect · 合法站内路径放行', () => {
  const valid: Array<[string, string]> = [
    ['工作台', '/dashboard'],
    ['带查询串', '/chat?conversation=42'],
    ['带哈希', '/knowledge/graph#entity'],
    ['带编码空格', '/a%20b'],
    ['带百分号查询（合法编码）', '/search?q=50%25off'],
    ['中文路径', '/知识库'],
    ['根路径', '/'],
  ]

  for (const [name, good] of valid) {
    it(`原样放行：${name}`, () => {
      expect(sanitizeInternalRedirect(good)).toBe(good)
      expect(resolvePostAuthTarget(good)).toBe(good)
    })
  }
})

describe('postAuthRedirect · 默认落点', () => {
  it('默认落点为 /dashboard（注册/登录未带 redirect 时的共享兜底）', () => {
    expect(DEFAULT_POST_AUTH_TARGET).toBe('/dashboard')
    expect(resolvePostAuthTarget(undefined)).toBe('/dashboard')
  })
})
