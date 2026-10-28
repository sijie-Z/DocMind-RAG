/**
 * 原文预览提示：legacy/mixed 与截断必须在弹窗可见。
 *
 * 背景：预览接口曾直接拼接增强后的 chunk_text 并静默截断；用户会把系统生成
 * 的背景摘要当成合同/制度原文。现在服务端返回 text_provenance/truncated，
 * 弹窗必须如实展示，不能只在 API 层「有字段」而界面沉默。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import naive from 'naive-ui'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import DocumentPreviewModal from '../components/DocumentPreviewModal.vue'

enableAutoUnmount(afterEach)

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

async function mountModal(extra: Record<string, unknown> = {}) {
  const wrapper = mount(DocumentPreviewModal, {
    props: {
      show: true,
      loading: false,
      content: '正文内容',
      ...extra,
    },
    global: {
      plugins: [naive, i18n],
      stubs: { teleport: true },
    },
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

describe('DocumentPreviewModal 原文口径提示', () => {
  it.each(['legacy_unverified', 'mixed'])('%s 显示原文缺失警告', async (prov) => {
    const wrapper = await mountModal({ textProvenance: prov })

    const alert = wrapper.find('[data-testid="chat-preview-provenance"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('不能直接作为原文证据')
  })

  it('parser_original 不显示原文缺失警告', async () => {
    const wrapper = await mountModal({ textProvenance: 'parser_original' })

    expect(wrapper.find('[data-testid="chat-preview-provenance"]').exists()).toBe(false)
  })

  it('旧后端/回滚缺失 provenance：有内容即按不可信提示（fail-closed）', async () => {
    const wrapper = await mountModal({})

    const alert = wrapper.find('[data-testid="chat-preview-provenance"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('不能直接作为原文证据')
  })

  it('无内容时不显示原文缺失警告', async () => {
    const wrapper = await mountModal({ content: '' })

    expect(wrapper.find('[data-testid="chat-preview-provenance"]').exists()).toBe(false)
  })

  it('truncated=true 显示截断提示，false 不显示', async () => {
    const truncated = await mountModal({ textProvenance: 'parser_original', truncated: true })
    expect(truncated.find('[data-testid="chat-preview-truncated"]').text()).toContain('已截断')

    const full = await mountModal({ textProvenance: 'parser_original', truncated: false })
    expect(full.find('[data-testid="chat-preview-truncated"]').exists()).toBe(false)
  })
})
