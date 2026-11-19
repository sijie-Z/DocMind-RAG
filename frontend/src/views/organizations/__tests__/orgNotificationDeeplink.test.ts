/**
 * 组织通知深链：?id=<org_id> → 打开对应组织详情抽屉。
 *
 * 后端组织类通知（加入/调岗/角色调整/移出组织，organizations.py 四处）都发
 * target_route="Organizations" + target_id=<org_id>。前端此前只消费
 * Knowledge 的深链、其它路由统一拼 ?id=，而 Organizations 页不读 query →
 * 点击通知只落到列表、看不到该组织。修复：加载完成后按 ?id= 打开详情。
 *
 * 变异点：删 openOrgFromQuery 调用 → 用例红；把 id 解析写死 → 负例（无 id
 * 或非法 id）红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

import OrganizationsView from '../index.vue'
import { useUserStore } from '@/stores/user'
import {
  getOrganizations,
  getOrganizationTree,
  getOrganizationMembers,
  getOrganizationDocuments,
} from '@/api/organization'
import zh from '@/locales/zh'

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: vi.fn(), info: vi.fn() }),
  }
})

vi.mock('@/api/organization', () => ({
  getOrganizations: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationMembers: vi.fn(),
  getOrganizationDocuments: vi.fn(),
  createOrganization: vi.fn(),
  updateOrganization: vi.fn(),
  deleteOrganization: vi.fn(),
  batchDeleteOrganizations: vi.fn(),
  addOrganizationMember: vi.fn(),
  updateOrganizationMemberRole: vi.fn(),
  removeOrganizationMember: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUsers: vi.fn().mockResolvedValue({ data: { users: [] } }) }
})

const ORG = {
  id: 3, name: 'Tester Org', color: '#18a058', description: '',
  parent_id: null, level: 1, sort_order: 0,
  created_at: '2026-10-08T05:40:55', updated_at: '2026-10-08T05:40:55',
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const DataTableStub = defineComponent({
  name: 'DataTable',
  setup() {
    return () => h('div')
  },
})

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(OrganizationsView) }),
})

async function mountAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/organizations', name: 'Organizations', component: { template: '<div />' } }],
  })
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  useUserStore().userInfo = {
    id: 1, username: 'root', email: 'root@example.com',
    isAdmin: true, is_superuser: true, createdAt: '', organizations: [],
  } as never
  const wrapper = mount(Host, {
    global: {
      plugins: [i18n, router, pinia],
      stubs: { DataTable: DataTableStub, teleport: true },
    },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

const vmOf = (w: Awaited<ReturnType<typeof mountAt>>) =>
  w.findComponent(OrganizationsView).vm as unknown as {
    showDetailDrawer: boolean
    selectedOrg: { id: number } | null
  }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOrganizations).mockResolvedValue({ data: { data: { data: [ORG], total: 1 } } } as never)
  vi.mocked(getOrganizationTree).mockResolvedValue({ data: { data: [ORG] } } as never)
  vi.mocked(getOrganizationMembers).mockResolvedValue({ data: { data: [] } } as never)
  vi.mocked(getOrganizationDocuments).mockResolvedValue({ data: { data: [] } } as never)
})

describe('组织页 · 通知深链 ?id=', () => {
  it('带合法 id → 打开该组织详情抽屉并加载成员/文档', async () => {
    const w = await mountAt('/organizations?id=3')
    const vm = vmOf(w)
    expect(vm.showDetailDrawer, '?id=3 应打开详情抽屉').toBe(true)
    expect(vm.selectedOrg?.id).toBe(3)
    expect(getOrganizationMembers).toHaveBeenCalledWith(3)
    w.unmount()
  })

  it('无 id → 不自动打开抽屉', async () => {
    const w = await mountAt('/organizations')
    expect(vmOf(w).showDetailDrawer).toBe(false)
    expect(getOrganizationMembers).not.toHaveBeenCalled()
    w.unmount()
  })

  it('非法 id → 不打开抽屉（不请求 NaN）', async () => {
    const w = await mountAt('/organizations?id=abc')
    expect(vmOf(w).showDetailDrawer).toBe(false)
    expect(getOrganizationMembers).not.toHaveBeenCalled()
    w.unmount()
  })
})
