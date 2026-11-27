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
    loading: 'loading', expiryLabel: 'Expiry', expiryNever: 'Never expires',
    expiryDays: '{days} days', expiresAt: 'Expires: {time}',
    expiredNotice: 'This share link has expired.',
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
    status.mockResolvedValue({
      data: { data: { shared: false, createdAt: null, expiresAt: null, expired: false } },
    })
    confirmYes.mockResolvedValue(true)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true,
    })
  })

  it('creates a share via API and displays token plus copyable link', async () => {
    create.mockResolvedValue({
      data: { data: { token: 'tok-123', url: '/share/tok-123', createdAt: '', expiresAt: null } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    // 新建默认 7 天（任务书口径）；后端仍接受 null = 永不过期。
    expect(create).toHaveBeenCalledWith('conv-1', 7)
    expect((wrapper.get('[data-testid="share-token"]').element as HTMLInputElement).value).toBe('tok-123')
    expect((wrapper.get('[data-testid="share-link"]').element as HTMLInputElement).value)
      .toBe(`${window.location.origin}/share/tok-123`)
    await wrapper.get('[data-testid="share-copy"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/share/tok-123`)
    expect(messageSuccess).toHaveBeenCalled()
  })

  it('passes the selected expiry days and renders the returned expiresAt', async () => {
    create.mockResolvedValue({
      data: {
        data: {
          token: 'tok-30',
          url: '/share/tok-30',
          createdAt: '',
          expiresAt: '2026-12-27T00:00:00+00:00',
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-expiry"]').setValue('30')
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('conv-1', 30)
    expect(wrapper.get('[data-testid="share-expires-at"]').text()).toContain('Expires:')
  })

  it('选择「永不过期」时传 null，后端语义为永久链接', async () => {
    create.mockResolvedValue({
      data: { data: { token: 'tok-perm', url: '/share/tok-perm', createdAt: '', expiresAt: null } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-expiry"]').setValue('0')
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('conv-1', null)
    expect(wrapper.find('[data-testid="share-expires-at"]').exists()).toBe(false)
  })

  it('状态返回 expiresAt 时显示过期时间；已过期显示过期态而不是「未分享」', async () => {
    status.mockResolvedValue({
      data: {
        data: {
          shared: true,
          createdAt: '2026-11-01T00:00:00+00:00',
          expiresAt: '2026-11-02T00:00:00+00:00',
          expired: true,
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.get('[data-testid="share-expires-at"]').text()).toContain('Expires:')
    expect(wrapper.find('[data-testid="share-expired"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('inactive')
    // 过期链接仍可撤销（token 还在）。
    expect(wrapper.get('[data-testid="share-revoke"]').attributes('disabled')).toBeUndefined()

    // 过期后重新生成必须给出安全默认 7 天，而不是静默变回永久链接。
    create.mockResolvedValue({
      data: { data: { token: 'tok-new', url: '/share/tok-new', createdAt: '', expiresAt: null } },
    })
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('conv-1', 7)
  })

  it('重新生成时回填现有过期档位，不会静默改成永久链接', async () => {
    status.mockResolvedValue({
      data: {
        data: {
          shared: true,
          createdAt: '2026-11-01T00:00:00+00:00',
          expiresAt: new Date(Date.now() + 30 * 86_400_000).toISOString(),
          expired: false,
        },
      },
    })
    create.mockResolvedValue({
      data: { data: { token: 'tok-r', url: '/share/tok-r', createdAt: '', expiresAt: null } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('conv-1', 30)
  })

  it('服务端 expired=true 时即使本地时间未到也显示过期态', async () => {
    status.mockResolvedValue({
      data: {
        data: {
          shared: true,
          createdAt: '2026-11-01T00:00:00+00:00',
          expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
          expired: true,
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="share-expired"]').exists()).toBe(true)
  })

  it('revokes via API, clears token and updates the panel state', async () => {
    status.mockResolvedValue({
      data: { data: { shared: true, createdAt: null, expiresAt: null, expired: false } },
    })
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

  it('撤销后有效期选择重置为 7 天', async () => {
    status.mockResolvedValue({
      data: { data: { shared: true, createdAt: null, expiresAt: null, expired: false } },
    })
    revoke.mockResolvedValue({ data: { data: { shared: false } } })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-expiry"]').setValue('30')
    await wrapper.get('[data-testid="share-revoke"]').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="share-expiry"]').element as HTMLSelectElement).value).toBe('7')
  })

  it('创建失败显示错误态且不显示 token', async () => {
    create.mockRejectedValue(new Error('boom'))
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-create"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="share-error"]').text()).toBe('create failed')
    expect(wrapper.find('[data-testid="share-token"]').exists()).toBe(false)
  })

  it('撤销失败显示错误态且保持已分享状态', async () => {
    status.mockResolvedValue({
      data: { data: { shared: true, createdAt: null, expiresAt: null, expired: false } },
    })
    revoke.mockRejectedValue(new Error('boom'))
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="share-revoke"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="share-error"]').text()).toBe('revoke failed')
    expect(wrapper.text()).toContain('active')
    expect(wrapper.find('[data-testid="share-token"]').exists()).toBe(false)
  })
})
