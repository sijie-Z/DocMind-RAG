/**
 * 调度任务管理页测试 —— 渲染 / 空态 / 错误态 / 危险确认 / 创建校验与载荷 /
 * run-now 后端语义 / 暂停恢复 / 结果弹窗 + 四语键完整性。
 *
 * 变异点：
 * - 列表渲染缺 plan 人话或 result 链接 → 渲染用例红；
 * - 空态/错误态分支不接数据源 → 对应用例红；
 * - 删除不经确认弹层或确认后不调 API → 危险确认用例红；
 * - 创建不做 schema 对齐校验（interval<60 也放行）→ 校验用例红；
 * - run-now 的 triggered=false 当成功 → 语义用例红；
 * - 载荷字段与 schedule_type 错配 → 载荷断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import {
  NMessageProvider,
  NDialogProvider,
  NButton,
  NDataTable,
  NDatePicker,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NRadio,
  NRadioButton,
  NRadioGroup,
  NResult,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  NTooltip,
} from 'naive-ui'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

import ScheduledTasksView from '../index.vue'

const apiMocks = vi.hoisted(() => ({
  listScheduledTasks: vi.fn(),
  createScheduledTask: vi.fn(),
  pauseScheduledTask: vi.fn(),
  resumeScheduledTask: vi.fn(),
  runNowScheduledTask: vi.fn(),
  deleteScheduledTask: vi.fn(),
}))

vi.mock('@/api/scheduledTask', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/scheduledTask')>()
  return { ...actual, ...apiMocks }
})

const agentMocks = vi.hoisted(() => ({ getTask: vi.fn() }))
vi.mock('@/api/agent', () => ({ agentApi: { getTask: agentMocks.getTask } }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () =>
    h(NDialogProvider, null, {
      default: () => h(NMessageProvider, null, { default: () => h(ScheduledTasksView) }),
    }),
})

// vitest 环境不加载 unplugin-vue-components：模板里的 n-* 需手动全局注册
// （既有页面测试同款做法），否则只渲染出未知标签、prop 型内容（description 等）丢失。
const naiveComponents = {
  NButton, NDataTable, NDatePicker, NDescriptions, NDescriptionsItem, NEmpty, NForm,
  NFormItem, NIcon, NInput, NInputNumber, NModal, NRadio, NRadioButton, NRadioGroup,
  NResult, NSelect, NSpace, NSpin, NSwitch, NTag, NTooltip,
}

function makeTask(overrides: Record<string, unknown> = {}) {
  return {
    id: 't1',
    name: '每日晨报',
    prompt: '汇总今天的新闻',
    schedule_type: 'cron',
    cron_expr: '0 9 * * *',
    interval_seconds: null,
    once_at: null,
    timezone: 'UTC',
    enabled: true,
    next_run_at: '2099-01-01T09:00:00',
    last_run_at: '2026-10-10T09:00:00',
    last_status: 'success',
    last_task_id: 'task-abc',
    last_error: null,
    run_count: 3,
    skipped_overlap_count: 0,
    created_by: 1,
    organization_id: 1,
    created_at: '2026-10-01T00:00:00',
    updated_at: '2026-10-10T09:00:00',
    ...overrides,
  }
}

// 未显式 unmount 的页面实例统一在用例结束时卸载：残留的打开弹窗会在后续用例里
// 借 naive-ui 的 z-index 重渲染把旧内容重新挂回 body，污染断言。
const mountedWrappers: Array<{ exists: () => boolean; unmount: () => void }> = []

async function mountView() {
  const wrapper = mount(Host, { global: { plugins: [i18n], components: naiveComponents } })
  mountedWrappers.push(wrapper)
  await flushAll()
  return wrapper
}

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.length = 0
})

/** 弹层/消息走 teleport + 过渡：多拍一拍。 */
async function flushAll() {
  await flushPromises()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
}

function findButtonByText(container: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) =>
    b.textContent?.includes(text),
  ) as HTMLButtonElement | undefined
}

describe('调度任务管理页', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [], total: 0 } })
  })

  it('渲染列表：名称 / cron 人话 / interval 人话 / 状态徽标 / 结果链接', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({
      data: {
        items: [
          makeTask(),
          makeTask({
            id: 't2',
            name: '巡检',
            schedule_type: 'interval',
            cron_expr: null,
            interval_seconds: 3600,
            enabled: false,
            last_task_id: null,
            last_status: null,
            last_run_at: null,
          }),
        ],
        total: 2,
      },
    })
    const wrapper = await mountView()
    const text = wrapper.text()
    expect(text).toContain('每日晨报')
    expect(text).toContain('每天 09:00') // cron → 人话
    expect(text).toContain('0 9 * * * · UTC') // 副行保留原始表达式 + 时区
    expect(text).toContain('巡检')
    expect(text).toContain('每 1 小时') // interval → 人话
    expect(text).toContain('已启用')
    expect(text).toContain('已暂停')
    expect(text).toContain('成功')
    expect(text).toContain('查看结果')
    expect(text).toContain('尚未运行')
  })

  it('空态：无任务 → n-empty + 新建入口', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('暂无定时任务')
    expect(findButtonByText(wrapper.element as ParentNode, '新建定时任务')).toBeTruthy()
  })

  it('筛选：切换启用状态 → 带 enabled 参数重新拉取（回到第一页）', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    const wrapper = await mountView()
    expect(apiMocks.listScheduledTasks).toHaveBeenLastCalledWith({ skip: 0, limit: 10 })

    const select = wrapper.findComponent(NSelect)
    expect(select.exists()).toBe(true)
    select.vm.$emit('update:value', 'enabled')
    await flushAll()
    expect(apiMocks.listScheduledTasks).toHaveBeenLastCalledWith({
      skip: 0,
      limit: 10,
      enabled: true,
    })
  })

  it('错误态：加载失败展示后端原文 + 重试重新拉取', async () => {
    apiMocks.listScheduledTasks.mockRejectedValueOnce({
      response: { data: { detail: '定时任务服务不可用' } },
    })
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('定时任务服务不可用')

    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    const retry = findButtonByText(wrapper.element as ParentNode, '刷新')
    expect(retry).toBeTruthy()
    retry!.click()
    await flushAll()
    expect(apiMocks.listScheduledTasks).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('每日晨报')
  })

  it('危险确认：删除先弹确认（含任务名），确认后才调 API', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    apiMocks.deleteScheduledTask.mockResolvedValue({ data: { success: true } })
    const wrapper = await mountView()

    wrapper.find('.row-delete-btn').trigger('click')
    await flushAll()

    // 确认弹层出现在 body（teleport）：文案带任务名；未确认前不得调 API
    expect(document.body.textContent).toContain('每日晨报')
    expect(document.body.textContent).toContain('不可恢复')
    expect(apiMocks.deleteScheduledTask).not.toHaveBeenCalled()

    const positive = findButtonByText(document.body, '确认')
    expect(positive).toBeTruthy()
    positive!.click()
    await flushAll()
    expect(apiMocks.deleteScheduledTask).toHaveBeenCalledWith('t1')
    expect(apiMocks.listScheduledTasks).toHaveBeenCalledTimes(2) // 删除后刷新
  })

  it('run-now：triggered=false 按后端语义提示（overlap 跳过），不伪装成功', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    apiMocks.runNowScheduledTask.mockResolvedValue({ data: { triggered: false, reason: 'overlap' } })
    const wrapper = await mountView()

    wrapper.find('.row-run-btn').trigger('click')
    await flushAll()

    expect(apiMocks.runNowScheduledTask).toHaveBeenCalledWith('t1')
    expect(document.body.textContent).toContain('上一轮运行尚未结束')
  })

  it('run-now：triggered=true 提示已触发并刷新列表', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    apiMocks.runNowScheduledTask.mockResolvedValue({ data: { triggered: true, reason: 'manual' } })
    const wrapper = await mountView()

    wrapper.find('.row-run-btn').trigger('click')
    await flushAll()

    expect(document.body.textContent).toContain('已触发运行')
    expect(apiMocks.listScheduledTasks).toHaveBeenCalledTimes(2)
  })

  it('暂停 / 恢复：按当前 enabled 走对应端点；409 原文呈现', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    apiMocks.pauseScheduledTask.mockResolvedValue({ data: {} })
    const wrapper = await mountView()

    wrapper.find('.row-toggle-btn').trigger('click')
    await flushAll()
    expect(apiMocks.pauseScheduledTask).toHaveBeenCalledWith('t1')
    wrapper.unmount()
    document.body.innerHTML = ''

    // 暂停态任务：恢复 → 后端 409（单次已跑完）原文展示
    apiMocks.listScheduledTasks.mockResolvedValue({
      data: {
        items: [
          makeTask({
            enabled: false,
            schedule_type: 'once',
            cron_expr: null,
            once_at: '2026-10-09T09:00:00',
            next_run_at: null,
            last_run_at: '2026-10-09T09:00:00',
          }),
        ],
        total: 1,
      },
    })
    apiMocks.resumeScheduledTask.mockRejectedValue({
      response: { data: { detail: '单次任务已运行完成，不能恢复；如需重跑请更新 once_at 重新武装' } },
    })
    const wrapper2 = await mountView()
    wrapper2.find('.row-toggle-btn').trigger('click')
    await flushAll()
    expect(apiMocks.resumeScheduledTask).toHaveBeenCalledWith('t1')
    expect(document.body.textContent).toContain('单次任务已运行完成')
    wrapper2.unmount()
  })

  it('创建：schema 对齐校验拦截空表单（不发请求），补齐后按 schedule_type 组载荷', async () => {
    apiMocks.createScheduledTask.mockResolvedValue({ data: makeTask() })
    const wrapper = await mountView()

    const createEntry = findButtonByText(wrapper.element as ParentNode, '新建定时任务')
    createEntry!.click()
    await flushAll()

    // 空表单直接保存 → 校验失败，不发请求
    const saveBtn = findButtonByText(document.body, '保存')
    expect(saveBtn).toBeTruthy()
    saveBtn!.click()
    await flushAll()
    expect(apiMocks.createScheduledTask).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('此项必填')

    // 填写 name / prompt / cron（默认类型 cron）→ 提交
    const nameInput = document.querySelector(
      'input[placeholder="例如：每日晨报"]',
    ) as HTMLInputElement
    nameInput.value = '每日播报'
    nameInput.dispatchEvent(new Event('input'))
    const cronInput = document.querySelector(
      'input[placeholder="例如：0 9 * * *"]',
    ) as HTMLInputElement
    cronInput.value = '30 8 * * 1-5'
    cronInput.dispatchEvent(new Event('input'))
    const promptArea = document.querySelector('textarea') as HTMLTextAreaElement
    promptArea.value = '整理昨日进展'
    promptArea.dispatchEvent(new Event('input'))
    await flushAll()

    findButtonByText(document.body, '保存')!.click()
    await flushAll()

    expect(apiMocks.createScheduledTask).toHaveBeenCalledTimes(1)
    expect(apiMocks.createScheduledTask.mock.calls[0][0]).toEqual({
      name: '每日播报',
      prompt: '整理昨日进展',
      schedule_type: 'cron',
      cron_expr: '30 8 * * 1-5',
      enabled: true,
    })
  })

  it('创建：interval 低于 60 秒被表单拦截（与后端 schema 同界）', async () => {
    const wrapper = await mountView()
    findButtonByText(wrapper.element as ParentNode, '新建定时任务')!.click()
    await flushAll()

    // 切到固定间隔
    const intervalRadio = Array.from(document.querySelectorAll('.n-radio-button')).find((el) =>
      el.textContent?.includes('固定间隔'),
    ) as HTMLElement
    expect(intervalRadio).toBeTruthy()
    intervalRadio.click()
    await flushAll()

    // 填好 name / prompt，让唯一失败项聚焦在间隔下限
    const nameInput = document.querySelector(
      'input[placeholder="例如：每日晨报"]',
    ) as HTMLInputElement
    nameInput.value = '巡检'
    nameInput.dispatchEvent(new Event('input'))
    const promptArea = document.querySelector('textarea') as HTMLTextAreaElement
    promptArea.value = '检查服务状态'
    promptArea.dispatchEvent(new Event('input'))
    await flushAll()

    // NInputNumber 的 :min 兜底会挡住手输越界值（不落模型）——用组件事件把
    // 30 直接写进模型，专测「表单校验与后端 schema 对齐」这道闸本身。
    const intervalNum = wrapper.findComponent(NInputNumber)
    expect(intervalNum.exists()).toBe(true)
    intervalNum.vm.$emit('update:value', 30)
    await flushAll()

    findButtonByText(document.body, '保存')!.click()
    await flushAll()
    expect(apiMocks.createScheduledTask).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('间隔不能小于 60 秒')
  })

  it('结果弹窗：查看结果 → 拉取 agent 任务并渲染状态/进度；读取失败如实提示', async () => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    agentMocks.getTask.mockResolvedValue({
      data: {
        success: true,
        data: {
          task_id: 'task-abc',
          state: 'completed',
          is_recoverable: false,
          progress: 1,
          completed_steps: 2,
          total_steps: 2,
          current_step_id: null,
          last_error: null,
          last_error_type: null,
          query: '汇总今天的新闻',
        },
      },
    })
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()

    expect(agentMocks.getTask).toHaveBeenCalledWith('task-abc')
    expect(document.body.textContent).toContain('task-abc')
    expect(document.body.textContent).toContain('完成')
    expect(document.body.textContent).toContain('2 / 2')

    // 第二次：读取失败（org/归属闸门）→ 诚实不可用
    agentMocks.getTask.mockRejectedValueOnce({
      response: { data: { detail: '当前用户未加入任何组织' } },
    })
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(document.body.textContent).toContain('结果不可用')
    expect(document.body.textContent).toContain('当前用户未加入任何组织')
  })
})

describe('结果弹窗：运行中 3s 轮询 / 关闭销毁 / 后台暂停 / 手动刷新', () => {
  /** GET /agent/tasks/{id} 的信封响应（apiUnwrap 再解一层）。 */
  function agentTaskResponse(overrides: Record<string, unknown> = {}) {
    return {
      data: {
        success: true,
        data: {
          task_id: 'task-abc',
          state: 'running',
          is_recoverable: false,
          progress: 0.5,
          completed_steps: 1,
          total_steps: 2,
          current_step_id: 'step-1',
          last_error: null,
          last_error_type: null,
          query: '汇总今天的新闻',
          ...overrides,
        },
      },
    }
  }

  function setVisibility(state: 'visible' | 'hidden') {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  }

  beforeEach(() => {
    apiMocks.listScheduledTasks.mockResolvedValue({ data: { items: [makeTask()], total: 1 } })
    // 只假 setInterval/clearInterval：页面内的过渡与 flushAll 的 setTimeout 保持真实
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  })

  afterEach(() => {
    delete (document as { visibilityState?: string }).visibilityState
    vi.useRealTimers()
  })

  it('轮询：运行中每 3s 自动拉取并刷新结果，到达终止态即停', async () => {
    agentMocks.getTask
      .mockResolvedValueOnce(agentTaskResponse())
      .mockResolvedValueOnce(agentTaskResponse({ completed_steps: 2 }))
      .mockResolvedValueOnce(agentTaskResponse({ state: 'completed', completed_steps: 2 }))

    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('运行中')

    await vi.advanceTimersByTimeAsync(3000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(3000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(3)
    // 弹窗未重开，结果自动更新为终态
    expect(document.body.textContent).toContain('完成')
    expect(document.body.textContent).toContain('2 / 2')

    await vi.advanceTimersByTimeAsync(9000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(3) // 终止态不再轮询
    expect(vi.getTimerCount()).toBe(0) // 无遗留定时器
    wrapper.unmount()
  })

  it('销毁：关闭弹窗立即停轮询（无泄漏定时器）', async () => {
    agentMocks.getTask.mockResolvedValue(agentTaskResponse())
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(vi.getTimerCount()).toBe(1) // 运行中 → 轮询已启动

    const resultModal = wrapper.findAllComponents(NModal).find((m) => m.props('show') === true)
    expect(resultModal).toBeTruthy()
    resultModal!.vm.$emit('update:show', false)
    await flushAll()
    expect(vi.getTimerCount()).toBe(0) // 关闭即销毁

    await vi.advanceTimersByTimeAsync(9000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('销毁：组件卸载时清理轮询定时器', async () => {
    agentMocks.getTask.mockResolvedValue(agentTaskResponse())
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(vi.getTimerCount()).toBe(1)

    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('后台暂停：visibilityState=hidden 时跳过拉取，恢复可见后继续', async () => {
    agentMocks.getTask.mockResolvedValue(agentTaskResponse())
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(6000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(1) // 后台标签页不烧请求

    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(3000)
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(2) // 回前台续上
    wrapper.unmount()
  })

  it('手动刷新：初次不可用后刷新成功 → 错误态恢复为结果展示', async () => {
    agentMocks.getTask.mockRejectedValueOnce({
      response: { data: { detail: '当前用户未加入任何组织' } },
    })
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(document.body.textContent).toContain('结果不可用')
    expect(document.body.textContent).toContain('当前用户未加入任何组织')

    agentMocks.getTask.mockResolvedValueOnce(agentTaskResponse({ state: 'completed' }))
    const refreshBtn = document.querySelector('.result-refresh-btn') as HTMLButtonElement
    expect(refreshBtn).toBeTruthy()
    refreshBtn.click()
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(2)
    // 错误态已被成功结果取代（footer 刷新按钮不因初次失败而失效）
    expect(document.body.textContent).not.toContain('当前用户未加入任何组织')
    expect(document.body.textContent).toContain('task-abc')
    expect(document.body.textContent).toContain('完成')
    wrapper.unmount()
  })

  it('手动刷新：一次点击触发一次拉取；进行中按钮 loading、完成后 toast 反馈', async () => {
    agentMocks.getTask.mockResolvedValueOnce(agentTaskResponse({ state: 'completed' }))
    const wrapper = await mountView()
    wrapper.find('.view-result-btn').trigger('click')
    await flushAll()
    expect(agentMocks.getTask).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0) // 完成态无轮询

    let resolveRefresh!: (value: unknown) => void
    agentMocks.getTask.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRefresh = resolve }),
    )
    const refreshBtn = document.querySelector('.result-refresh-btn') as HTMLButtonElement
    expect(refreshBtn).toBeTruthy()
    refreshBtn.click()
    await flushAll()

    expect(agentMocks.getTask).toHaveBeenCalledTimes(2) // 一次点击 = 一次拉取
    expect(
      (document.querySelector('.result-refresh-btn') as HTMLElement).classList
        .contains('n-button--loading'),
    ).toBe(true) // 进行中反馈

    resolveRefresh(agentTaskResponse({ state: 'completed', completed_steps: 2 }))
    await flushAll()
    expect(
      (document.querySelector('.result-refresh-btn') as HTMLElement).classList
        .contains('n-button--loading'),
    ).toBe(false) // 完成态：按钮复位
    expect(document.body.textContent).toContain('结果已刷新')
    wrapper.unmount()
  })
})

describe('本地化键完整性（本特性新增键）', () => {
  const locales: Array<[string, Record<string, unknown>]> = [
    ['zh', zh as unknown as Record<string, unknown>],
    ['en', en as unknown as Record<string, unknown>],
    ['ja', ja as unknown as Record<string, unknown>],
    ['fr', fr as unknown as Record<string, unknown>],
  ]

  const keys = [
    'menu.scheduledTasks',
    'menu.mcpServers',
    'scheduler.create',
    'scheduler.noData',
    'scheduler.columns.schedule',
    'scheduler.statusFinished',
    'scheduler.lastStatus.failed',
    'scheduler.runNowReason.overlap',
    'scheduler.deleteConfirm',
    'scheduler.plan.intervalMinutes',
    'scheduler.cron.everyMinute',
    'scheduler.cron.dailyAt',
    'scheduler.cron.weekdayRange',
    'scheduler.cron.weekdays.0',
    'scheduler.cron.weekdays.6',
    'scheduler.cron.months.1',
    'scheduler.cron.months.12',
    'scheduler.modal.cronPlaceholder',
    'scheduler.modal.type.once',
    'scheduler.validation.intervalMin',
    'scheduler.result.taskState.rejected',
    'scheduler.result.unavailable',
    'scheduler.result.refresh',
    'scheduler.result.refreshed',
    'mcp.readOnlyNotice',
    'mcp.noServers',
    'mcp.toolCount',
    'mcp.lastError',
    'mcp.state.connected',
    'mcp.state.not_connected',
  ]

  function lookup(messages: Record<string, unknown>, key: string): unknown {
    return key
      .split('.')
      .reduce<unknown>(
        (acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined),
        messages,
      )
  }

  it('四语（zh/en/ja/fr）键齐且非空', () => {
    for (const [code, messages] of locales) {
      for (const key of keys) {
        const value = lookup(messages, key)
        expect(typeof value === 'string' && value.length > 0, `${code} 缺 ${key}`).toBe(true)
      }
    }
  })
})
