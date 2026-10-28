/**
 * 知识库详情弹窗的原文口径提示（与聊天来源预览同一条服务端契约）。
 *
 * 旧版分块（无 parser 原文）或服务端截断必须在界面上可见——否则用户会把
 * 可能带【文档背景】生成摘要的文本当作原文证据。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import naive from 'naive-ui'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import type { KnowledgeBase } from '@/api/knowledge'
import KnowledgeDetailModals from '../components/KnowledgeDetailModals.vue'

enableAutoUnmount(afterEach)

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

function makeDoc(extra: Partial<KnowledgeBase> = {}): KnowledgeBase {
  return {
    id: 'doc-1',
    title: '采购合同',
    file_name: 'contract.pdf',
    file_type: 'pdf',
    file_size: 1024,
    tags: [],
    created_at: '2026-10-01T00:00:00',
    updated_at: '2026-10-01T00:00:00',
    status: 'indexed',
    preview_content: '第一条 付款期限为 30 天。',
    ...extra,
  }
}

async function mountModals(doc: KnowledgeBase) {
  const wrapper = mount(KnowledgeDetailModals, {
    props: {
      showDetail: true,
      showError: false,
      currentDoc: doc,
      currentErrorDetail: '',
      detailLoading: false,
      getFileIconColor: () => '#000',
      getStatusType: () => 'success' as const,
      getStatusLabel: () => '已完成',
      formatFileSize: () => '1 KB',
      formatDate: () => '2026-10-01',
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

describe('KnowledgeDetailModals 原文口径提示', () => {
  it.each(['legacy_unverified', 'mixed'] as const)('%s 显示原文缺失警告', async (prov) => {
    const wrapper = await mountModals(makeDoc({ preview_provenance: prov }))

    const alert = wrapper.find('[data-testid="knowledge-preview-provenance"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('不能直接作为原文证据')
  })

  it('parser_original 不显示警告', async () => {
    const wrapper = await mountModals(makeDoc({ preview_provenance: 'parser_original' }))

    expect(wrapper.find('[data-testid="knowledge-preview-provenance"]').exists()).toBe(false)
  })

  it('缺失 provenance 的旧后端响应：有内容即按不可信提示（fail-closed）', async () => {
    const wrapper = await mountModals(makeDoc({}))

    const alert = wrapper.find('[data-testid="knowledge-preview-provenance"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('不能直接作为原文证据')
  })

  it('preview_truncated=true 显示截断提示', async () => {
    const wrapper = await mountModals(
      makeDoc({ preview_provenance: 'parser_original', preview_truncated: true })
    )

    expect(wrapper.find('[data-testid="knowledge-preview-truncated"]').text()).toContain('已截断')
  })
})
