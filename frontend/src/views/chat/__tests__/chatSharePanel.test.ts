import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'

const { status, create, revoke } = vi.hoisted(() => ({
  status: vi.fn(), create: vi.fn(), revoke: vi.fn(),
}))
vi.mock('@/api/share', () => ({
  getConversationShareStatus: status,
  createConversationShare: create,
  revokeConversationShare: revoke,
}))

const messageSuccess = vi.fn()
const confirmYes = vi.fn()
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => ({ success: messageSuccess, error: vi.fn() }) }))
vi.mock('@/utils/confirm', () => ({ useConfirmDangerousAction: () => confirmYes }))

import SharePanel from '../components/SharePanel.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en: { chat: { share: {
    title: 'Share conversation', description: 'desc', linkLabel: 'Link', tokenLabel: 'Token',
    onceNotice: 'once', activeNotice: 'active', inactive: 'inactive', create: 'Create',
    regenerate: 'Regenerate', copyLink: 'Copy link', revoke: 'Revoke', revokeConfirm: 'Confirm?',
    created: 'created', revoked: 'revoked', copied: 'copied', createFailed: 'create failed',
    revokeFailed: 'revoke failed', copyFailed: 'copy failed', loadFailed: 'load failed',
    loading: 'loading',
  } } } },
})

const ModalStub = defineComponent({
  props: { show: Boolean },
  setup(props, { slots }) { return () => h('div', { class: 'modal' }, props.show ? slots.default?.() : []) },
})
const CardStub = defineComponent({ setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const ButtonStub = defineComponent({
  props: { disabled: Boolean, type: String, loading: Boolean },
  emits: ['click'],
  setup(props, { slots, attrs, emit }) {
    return () => h('button', { ...attrs, disabled: props.disabled, onClick: () => emit('click') }, slots.default?.())
  },
})

function mountPanel() {
  return mount(SharePanel, {
    props: { show: true, conversationId: 'conv-1' },
    global: {
      plugins: [i18n],
      stubs: { 'n-modal': ModalStub, 'n-card': CardStub, 'n-button': ButtonStub },
    },
  })
}

describe('SharePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    status.mockResolvedValue({ data: { data: { shared: false, createdAt: null } } })
    confirmYes.mockResolvedValue(true)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true,
    })
  })

  it('creates a share via API and displays token plus copyable link', async () => {
    create.mockResolvedValue({ data: { data: { token: 'tok-123', url: '/share/tok-123', createdAt: '' } } })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('conv-1')
    expect((wrapper.get('[data-testid="share-token"]').element as HTMLInputElement).value).toBe('tok-123')
    expect((wrapper.get('[data-testid="share-link"]').element as HTMLInputElement).value)
      .toBe(`${window.location.origin}/share/tok-123`)
    await wrapper.get('[data-testid="share-copy"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/share/tok-123`)
    expect(messageSuccess).toHaveBeenCalled()
  })

  it('revokes via API, clears token and updates the panel state', async () => {
    status.mockResolvedValue({ data: { data: { shared: true, createdAt: null } } })
    revoke.mockResolvedValue({ data: { data: { shared: false } } })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.get('[data-testid="share-revoke"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="share-revoke"]').trigger('click')
    await flushPromises()
    expect(revoke).toHaveBeenCalledWith('conv-1')
    expect(wrapper.find('[data-testid="share-token"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('inactive')
    expect(wrapper.emitted('revoked')).toBeTruthy()
  })
})
