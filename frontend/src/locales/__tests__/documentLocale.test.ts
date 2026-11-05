/**
 * 文档语言同步：切换 locale 时 <html lang> / document.title / meta description
 * 必须跟随，否则屏幕阅读器与浏览器翻译始终按中文处理。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { setLanguage } from '@/locales'

describe('document locale', () => {
  afterEach(async () => {
    await setLanguage('zh')
  })

  it('切换英文时同步 html lang 与标题', async () => {
    await setLanguage('en')
    expect(document.documentElement.lang).toBe('en')
    expect(document.title).toContain('DocMind')
    expect(document.title).not.toContain('知识库')
  })

  it('切回中文时恢复 zh-CN 与中文标题', async () => {
    await setLanguage('en')
    await setLanguage('zh')
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.title).toContain('文档问答与合同核验平台')
  })
})
