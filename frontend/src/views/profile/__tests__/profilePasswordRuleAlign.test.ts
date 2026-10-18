/**
 * R2 · 个人中心改密规则与后端口径对齐（最小长度 ≥8）。
 *
 * 后端口径：ChangePasswordRequest.new_password min_length=8（backend/app/schemas/auth.py）。
 * 前端个人中心曾为 min: 6 + 「6-20」文案 —— 6 位新密码可通过前端校验后被后端 422 拒绝。
 *
 * 判别点：把 useProfilePage 的 passwordFormRules.newPassword.min 改回 6（或四语文案
 * 改回 6-20），本文件「6 位被拦」与四语文案断言即红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NForm, NFormItem, NInput, NMessageProvider } from 'naive-ui'

import { useProfilePage } from '../composables/useProfilePage'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserProfile: vi.fn().mockResolvedValue({
      data: {
        id: 1, username: 'tester', email: 't@t', nickname: 'tester', full_name: null,
        phone: null, avatar_url: null, bio: null, organization_id: 3, role: 'user',
        is_active: true, created_at: '2026-10-08T05:39:53', last_login_at: null,
      },
    }),
    getUserStats: vi.fn().mockResolvedValue({
      data: { conversation_count: 0, message_count: 0, file_count: 0, knowledge_count: 0, storage_used: 0 },
    }),
    getUserActivities: vi.fn().mockResolvedValue({ data: [] }),
    getMySessions: vi.fn().mockResolvedValue({ data: [] }),
    getMyActivityLogs: vi.fn().mockResolvedValue({ data: [] }),
    updateUserProfile: vi.fn(),
    updatePassword: vi.fn(),
    uploadAvatar: vi.fn(),
    revokeMySession: vi.fn(),
  }
})

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: vi.fn(), info: vi.fn() }),
  }
})

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
  },
}))

vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ providers: [], current: null }),
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

let capturedApi: ReturnType<typeof useProfilePage>

const Harness = defineComponent({
  setup() {
    capturedApi = useProfilePage()
    return () => null
  },
})

const wrappers: ReturnType<typeof mount>[] = []

function mountHarness() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(Harness) }),
  })
  const wrapper = mount(Host, { global: { plugins: [pinia, i18n] } })
  wrappers.push(wrapper)
  return wrapper
}

/** 用真实 NForm 校验 newPassword 规则，返回 { ok, message } */
async function validateNewPassword(password: string) {
  mountHarness()
  await flushPromises()

  const model = reactive({
    currentPassword: 'old-password',
    newPassword: password,
    confirmPassword: password,
  })
  const FormHost = defineComponent({
    setup() {
      return () =>
        h(NMessageProvider, null, {
          default: () =>
            h(
              NForm,
              { model, rules: capturedApi.passwordFormRules },
              {
                default: () =>
                  h(
                    NFormItem,
                    { path: 'newPassword' },
                    { default: () => h(NInput, { value: password }) },
                  ),
              },
            ),
        })
    },
  })
  const wrapper = mount(FormHost, { global: { plugins: [i18n] } })
  wrappers.push(wrapper)
  await flushPromises()

  try {
    await wrapper.findComponent(NForm).vm.validate()
    return { ok: true, message: '' }
  } catch (errors) {
    // naive-ui 的 reject 形如 ValidateError[][]（每个表单项一组），这里拍平取文案
    const groups = (Array.isArray(errors) ? errors : [errors]) as Array<
      Array<{ message?: string }> | { message?: string }
    >
    const messages = groups.flatMap((group) =>
      Array.isArray(group) ? group.map((e) => e?.message ?? '') : [group?.message ?? ''],
    )
    return { ok: false, message: messages.join(' | ') }
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  document.body.innerHTML = ''
})

function resolveKey(messages: unknown, path: string): unknown {
  let node: unknown = messages
  for (const seg of path.split('.')) {
    if (node && typeof node === 'object' && seg in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[seg]
    } else {
      return undefined
    }
  }
  return node
}

describe('个人中心改密最小长度与后端口径对齐（≥8）', () => {
  it('6 位新密码被前端校验拦下，并提示至少 8 位', async () => {
    const result = await validateNewPassword('123456')
    expect(result.ok, '6 位密码不应通过前端校验').toBe(false)
    expect(result.message).toContain('8')
    expect(result.message).toContain('20')
  })

  it('8 位新密码通过校验（防过度收紧）', async () => {
    const result = await validateNewPassword('12345678')
    expect(result.ok, result.message).toBe(true)
  })

  it('规则上下限为 8-20，且四语文案同步（不再出现 6-20）', () => {
    const rules = capturedApi.passwordFormRules.newPassword as Array<Record<string, unknown>>
    const lengthRule = rules.find((rule) => typeof rule.min === 'number')
    expect(lengthRule, '缺少长度规则').toBeTruthy()
    expect(lengthRule?.min).toBe(8)
    expect(lengthRule?.max).toBe(20)

    const locales: Array<[string, unknown]> = [
      ['zh', zh],
      ['en', en],
      ['ja', ja],
      ['fr', fr],
    ]
    for (const [name, messages] of locales) {
      const text = resolveKey(messages, 'profile.validation.passwordLength')
      expect(typeof text, `${name} 缺少 profile.validation.passwordLength`).toBe('string')
      expect(text as string, `${name} 文案未同步 8`).toContain('8')
      expect(text as string, `${name} 文案未同步 20`).toContain('20')
      expect(text as string, `${name} 文案仍含 6`).not.toContain('6')
    }
  })
})
