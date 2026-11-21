import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'

const getShared = vi.hoisted(() => vi.fn())
vi.mock('@/api/share', () => ({ getSharedConversation: getShared }))

const routePush = vi.fn()
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { token: 'public-token' } }), useRouter: () => ({ push: routePush }) }))

import ReadonlyShare from '../readonly.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en: { chat: {
    share: { title: 'Shared', readOnlyNotice: 'read only', loading: 'loading', unavailable: 'unavailable', sources: 'Sources' },
    roleUser: 'User', roleAssistant: 'Assistant', sourceDoc: 'Document',
  } } },
})
const MarkdownStub = defineComponent({
  props: { content: String },
  setup(props) { return () => h('div', { class: 'md' }, props.content) },
})

function mountPage() {
  return mount(ReadonlyShare, { global: { plugins: [i18n], stubs: { Markdown: MarkdownStub } } })
}

describe('public read-only share page', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders the snapshot for an unauthenticated visitor', async () => {
    getShared.mockResolvedValue({ data: { success: true, data: {
      id: 'conv-1', title: 'Private title', createdAt: '',
      messages: [
        { id: 'm1', content: 'secret question', messageType: 'user' },
        { id: 'm2', content: 'answer', messageType: 'assistant', sources: [] },
      ],
    } } })
    const wrapper = mountPage()
    await flushPromises()
    expect(getShared).toHaveBeenCalledWith('public-token')
    expect(wrapper.text()).toContain('Private title')
    expect(wrapper.text()).toContain('secret question')
    expect(wrapper.text()).toContain('answer')
    expect(wrapper.findAll('.md')).toHaveLength(2)
    expect(wrapper.text()).toContain('read only')
  })

  it('shows an unavailable notice when the token is invalid or revoked', async () => {
    getShared.mockRejectedValue(new Error('404'))
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('unavailable')
    expect(wrapper.text()).not.toContain('loading')
  })
})
