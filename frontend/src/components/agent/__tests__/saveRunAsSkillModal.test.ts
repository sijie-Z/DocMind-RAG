/**
 * 从 completed run 保存为组织技能（批次十）——弹窗契约。
 *
 * 变异点：
 * - 提交不调用 `orgSkillsApi.propose` / 不带 source_task_id → 用例红；
 * - 空名称不拦（仍发请求）→ 用例红；
 * - API 失败不显示错误 / 不清空结果 → 用例红；
 * - 成功后不发 saved（父组件无法跳转待审队列）→ 用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { NAlert, NButton, NInput, NModal } from 'naive-ui'

import SaveRunAsSkillModal from '../SaveRunAsSkillModal.vue'
import { orgSkillsApi } from '@/api/orgSkills'
import zh from '@/locales/zh'

const messageMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageMocks,
}))
vi.mock('@/api/orgSkills', () => ({
  orgSkillsApi: { propose: vi.fn() },
}))

const api = vi.mocked(orgSkillsApi, true)
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountModal(props: { show?: boolean; taskId?: string; defaultName?: string } = {}) {
  return mount(SaveRunAsSkillModal, {
    props: {
      show: true,
      taskId: 'task-1',
      defaultName: '季度财务总结',
      ...props,
    },
    global: {
      plugins: [i18n],
      components: { NAlert, NButton, NInput, NModal },
      stubs: { teleport: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.propose.mockResolvedValue({ data: { success: true, data: {} } } as never)
})

describe('SaveRunAsSkillModal', () => {
  it('默认名来自任务查询；提交带 source_task_id 并发出 saved', async () => {
    const wrapper = mountModal()
    const nameInput = wrapper.find('[data-testid="save-skill-name"] input')
    expect((nameInput.element as HTMLInputElement).value).toBe('季度财务总结')

    await wrapper.find('[data-testid="save-skill-description"] textarea').setValue('季度复盘场景')
    await wrapper.find('[data-testid="save-skill-submit"]').trigger('click')
    await flushPromises()

    expect(api.propose).toHaveBeenCalledWith({
      name: '季度财务总结',
      description: '季度复盘场景',
      source_task_id: 'task-1',
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(wrapper.emitted('update:show')?.[0]).toEqual([false])
    expect(messageMocks.success).toHaveBeenCalled()
  })

  it('空名称：前端拦截，不发请求', async () => {
    const wrapper = mountModal()
    await wrapper.find('[data-testid="save-skill-name"] input').setValue('   ')
    await wrapper.find('[data-testid="save-skill-submit"]').trigger('click')
    await flushPromises()

    expect(api.propose).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请输入技能名称')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('API 失败：显示错误且不发 saved', async () => {
    api.propose.mockRejectedValue({
      response: { data: { message: '来源任务不存在或不可用于技能提议' } },
    })
    const wrapper = mountModal()
    await wrapper.find('[data-testid="save-skill-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('来源任务不存在或不可用于技能提议')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
})
