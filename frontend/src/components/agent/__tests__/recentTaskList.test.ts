/**
 * 任务记录列表（任务列表侧 has_replay 的可见消费面："有回放可看"标出 + 回放入口）。
 *
 * 变异点：has_replay 标记不渲染（全部当无回放）→ 首条用例红；
 * 回放按钮不发 view-replay → 点击用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import i18n from '@/locales'

import RecentTaskList from '../RecentTaskList.vue'
import type { AgentTaskListItem } from '@/types/agent'

function task(overrides: Partial<AgentTaskListItem>): AgentTaskListItem {
  return {
    task_id: 'task-1',
    state: 'completed',
    is_recoverable: false,
    progress: 1,
    completed_steps: 2,
    total_steps: 2,
    current_step_id: null,
    last_error: null,
    last_error_type: null,
    query: '分析文档',
    has_replay: true,
    created_at: '2026-10-10T09:30:00',
    updated_at: '2026-10-10T09:31:00',
    ...overrides,
  }
}

function mountC(tasks: AgentTaskListItem[], loading = false) {
  return mount(RecentTaskList, { props: { tasks, loading }, global: { plugins: [i18n] } })
}

describe('RecentTaskList · 任务记录', () => {
  it('有回放的任务亮「回放」按钮；无回放如实标「无回放」', () => {
    const w = mountC([
      task({ task_id: 't-with', has_replay: true }),
      task({ task_id: 't-without', has_replay: false, state: 'failed', query: '会炸的任务' }),
    ])
    expect(w.findAll('.task-row')).toHaveLength(2)
    const rows = w.findAll('.task-row')
    expect(rows[0].find('.task-replay-btn').exists()).toBe(true)
    expect(rows[0].find('.task-no-replay').exists()).toBe(false)
    expect(rows[1].find('.task-replay-btn').exists()).toBe(false)
    expect(rows[1].text()).toContain('无回放')
    // 状态如实：完成 / 失败
    expect(rows[0].text()).toContain('已完成')
    expect(rows[1].text()).toContain('失败')
    w.unmount()
  })

  it('点击「回放」携带 task_id 发出 view-replay', async () => {
    const w = mountC([task({ task_id: 't-open', has_replay: true })])
    await w.find('.task-replay-btn').trigger('click')
    expect(w.emitted('view-replay')).toEqual([['t-open']])
    w.unmount()
  })

  it('空列表 / 加载中：诚实状态文案', () => {
    const empty = mountC([])
    expect(empty.text()).toContain('暂无任务记录')
    empty.unmount()
    const loading = mountC([], true)
    expect(loading.text()).toContain('正在加载任务')
    loading.unmount()
  })
})
