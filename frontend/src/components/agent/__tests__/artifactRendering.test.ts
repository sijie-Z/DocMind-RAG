/**
 * 工件渲染升级（体验第四波）：ToolCallCard / RunReportBlock 的富渲染与下载。
 * - diff 结果 → DiffView 差异视图（增删行着色 + 类型徽标）
 * - JSON 结果 → 美化 + hljs 高亮；截断预览诚实地回退纯文本，展开后富渲染
 * - Markdown 结果 → 复用聊天页 Markdown 组件（标题成真标题）
 * - 下载：按类型落盘（.patch/.json/.md）
 *
 * 变异点：ArtifactViewer 去掉 diff 分支 → diff 用例红；kind 恒 text → 徽标用例红；
 * 下载文件名不按类型给扩展名 → 下载用例红。
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import ToolCallCard from '@/components/agent/ToolCallCard.vue'
import RunReportBlock from '@/components/agent/RunReportBlock.vue'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const DIFF = '--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-old()\n+new()\n keep\n'

function mountCard(resultText: string, toolName = 'search') {
  return mount(ToolCallCard, {
    props: { toolName, resultText, status: 'success' as const },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ToolCallCard · 工件渲染升级', () => {
  it('diff 结果 → 差异视图（增删行着色 + 徽标「差异」）', () => {
    const wrapper = mountCard(DIFF, 'edit_file')
    expect(wrapper.find('[data-testid="diff-view"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="artifact-kind"]').text()).toBe('差异')
    expect(wrapper.find('.diff-del').exists()).toBe(true)
    expect(wrapper.find('.diff-add').exists()).toBe(true)
    // 差异视图替代裸 <pre>：不再有全文裸文本节点
    expect(wrapper.find('.artifact-plain').exists()).toBe(false)
  })

  it('JSON 结果 → 美化高亮代码块', () => {
    const wrapper = mountCard('{"name":"docmind","tags":["a","b"]}')
    expect(wrapper.find('[data-testid="artifact-kind"]').text()).toBe('JSON')
    const code = wrapper.find('.artifact-code')
    expect(code.exists()).toBe(true)
    expect(code.text()).toContain('"name"')
    // 美化：缩进后含换行
    expect(code.text()).toContain('\n')
  })

  it('Markdown 结果 → 复用 Markdown 组件渲染（标题成真标题）', () => {
    const wrapper = mountCard('# 结论\n\n- 要点一\n- 要点二')
    expect(wrapper.find('[data-testid="artifact-kind"]').text()).toBe('Markdown')
    expect(wrapper.find('.markdown-body h1').exists()).toBe(true)
  })

  it('下载按钮：diff 按类型落盘 edit_file.patch', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mountCard(DIFF, 'edit_file')
    await wrapper.find('[data-testid="artifact-download"]').trigger('click')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const a = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(a.download).toBe('edit_file.patch')
  })

  it('超长 JSON 截断预览回退纯文本；展开全部后富渲染', async () => {
    const longJson = '{"data":"' + 'x'.repeat(400) + '"}'
    const wrapper = mountCard(longJson, 'fetch_data')
    expect(wrapper.find('.artifact-plain').exists()).toBe(true)
    expect(wrapper.find('.artifact-code').exists()).toBe(false)
    await wrapper.find('.expand-btn').trigger('click')
    expect(wrapper.find('.artifact-code').exists()).toBe(true)
    expect(wrapper.find('.artifact-plain').exists()).toBe(false)
  })

  it('纯文本结果 → 等宽纯文本（无富渲染误判）', () => {
    const wrapper = mountCard('操作完成，共处理 3 条记录。')
    expect(wrapper.find('[data-testid="artifact-kind"]').text()).toBe('文本')
    expect(wrapper.find('.artifact-plain').exists()).toBe(true)
    expect(wrapper.find('[data-testid="diff-view"]').exists()).toBe(false)
  })
})

describe('RunReportBlock · 报告 Markdown 渲染 + 导出', () => {
  it('报告正文 Markdown 渲染（## 标题成真标题）+ 摘要含 Tokens', () => {
    const wrapper = mount(RunReportBlock, {
      props: { report: '## 步骤\n\n- [ok] 搜索\n- [ok] 整理', totalTokens: 14176 },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.run-report').exists()).toBe(true)
    expect(wrapper.find('summary').text()).toContain('运行报告')
    expect(wrapper.find('summary').text()).toContain('Tokens: 14176')
    expect(wrapper.find('.run-report-body h2').exists()).toBe(true)
    // 正文不再是裸 <pre>
    expect(wrapper.find('.run-report pre').exists()).toBe(false)
  })

  it('导出按钮：run_report.md 一键落盘', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mount(RunReportBlock, {
      props: { report: '## 步骤\n- 完成' },
      global: { plugins: [i18n] },
    })
    await wrapper.find('[data-testid="run-report-export"]').trigger('click')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const a = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(a.download).toBe('run_report.md')
  })
})
