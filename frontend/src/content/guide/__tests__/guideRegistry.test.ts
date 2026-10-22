/**
 * 使用文档注册表判别：
 * - id 唯一、分组有序、文档非空且包含二级标题（保证页内目录可用）；
 * - 每篇标题在 zh/en/ja/fr 四语均可解析（导航标题走四语词条）；
 * - 每篇带「依据（代码事实）」小节（内容口径可追溯）；
 * - 不得出现未支持功能的宣称（PPT/弱口令），且关键支持格式声明必须存在
 *   （PDF / Word / .docx / .xlsx / Markdown / CSV / OCR 等，与解析器事实一致）。
 */
import { describe, it, expect } from 'vitest'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'
import { GUIDE_DOCS, GUIDE_GROUP_ORDER } from '@/content/guide'

type Messages = Record<string, unknown>

function resolve(messages: Messages, path: string): unknown {
  let node: unknown = messages
  for (const segment of path.split('.')) {
    if (node && typeof node === 'object' && segment in (node as Messages)) {
      node = (node as Messages)[segment]
    } else {
      return undefined
    }
  }
  return node
}

const LOCALES: Array<{ name: string; messages: Messages }> = [
  { name: 'zh', messages: zh as Messages },
  { name: 'en', messages: en as Messages },
  { name: 'ja', messages: ja as Messages },
  { name: 'fr', messages: fr as Messages }
]

describe('使用文档 · 注册表完整性', () => {
  it('至少 15 篇文档，且五个分组都有内容', () => {
    expect(GUIDE_DOCS.length).toBeGreaterThanOrEqual(15)
    for (const group of GUIDE_GROUP_ORDER) {
      expect(GUIDE_DOCS.some((doc) => doc.group === group), `分组 ${group} 为空`).toBe(true)
    }
  })

  it('文档 id 唯一，标题键唯一，阅读顺序按分组顺序聚集', () => {
    const ids = GUIDE_DOCS.map((doc) => doc.id)
    expect(new Set(ids).size).toBe(ids.length)
    const titleKeys = GUIDE_DOCS.map((doc) => doc.titleKey)
    expect(new Set(titleKeys).size).toBe(titleKeys.length)

    let lastGroupIndex = 0
    for (const doc of GUIDE_DOCS) {
      const index = GUIDE_GROUP_ORDER.indexOf(doc.group)
      expect(index, `未知分组 ${doc.group}`).toBeGreaterThanOrEqual(0)
      expect(index).toBeGreaterThanOrEqual(lastGroupIndex)
      lastGroupIndex = index
    }
  })

  it('每篇正文非空且至少有一个二级标题（页内目录与锚点依赖 h2/h3）', () => {
    for (const doc of GUIDE_DOCS) {
      expect(doc.content.trim().length, `${doc.id} 正文为空`).toBeGreaterThan(0)
      expect(doc.content, `${doc.id} 缺少二级标题`).toMatch(/^##\s+\S+/m)
      expect(doc.content, `${doc.id} 缺少「依据（代码事实）」小节`).toContain('## 依据（代码事实）')
    }
  })

  it('标题在四语中都存在且非空（不回落裸键）', () => {
    for (const doc of GUIDE_DOCS) {
      for (const { name, messages } of LOCALES) {
        const value = resolve(messages, doc.titleKey)
        expect(typeof value, `${name} 缺 ${doc.titleKey}`).toBe('string')
        expect((value as string).length).toBeGreaterThan(0)
        expect(value).not.toBe(doc.titleKey)
      }
    }
  })
})

describe('使用文档 · 事实口径', () => {
  const allContent = GUIDE_DOCS.map((doc) => doc.content).join('\n')

  it('不出现未支持功能的宣称（PPT/演示文稿、弱口令）', () => {
    expect(allContent).not.toContain('PowerPoint')
    expect(allContent).not.toContain('支持 PPT')
    expect(allContent).not.toContain('123456')
    expect(allContent).not.toContain('guest /')
  })

  it('关键格式与 OCR 口径与解析器一致', () => {
    for (const token of ['PDF', 'Word', '.docx', '.xlsx', 'Markdown', 'CSV', 'OCR']) {
      expect(allContent, `缺少格式声明 ${token}`).toContain(token)
    }
    expect(allContent).toContain('不支持 .ppt / .pptx')
    expect(allContent).toContain('只做 OCR')
  })

  it('保留排错路径关键词（重新解析 / 最近失败文档重建 / 红色错误提示条）', () => {
    for (const token of ['重新解析', '最近失败文档重建', '红色错误提示条']) {
      expect(allContent, `缺少排错关键词 ${token}`).toContain(token)
    }
  })

  it('保留注册与成员管理关键词（去注册 / 用户管理）', () => {
    expect(allContent).toContain('去注册')
    expect(allContent).toContain('用户管理')
  })
})
