/**
 * 回归：用例结束时仍挂起的真实定时器必须在 jsdom 拆除前被清掉。
 *
 * 背景：naive-ui 2.45.3 的 MessageEnvironment / NotificationEnvironment 在消息
 * 挂载时用 `window.setTimeout(hide, duration)` 排自动消失定时器（默认 3s），但
 * 环境组件卸载时不取消。CI 上 199 个测试文件全部通过后，该 timer 才在 jsdom
 * 拆除之后到期，`hide()` 里的 `window.clearTimeout` 抛
 * `ReferenceError: window is not defined`，整个 Frontend Tests job 因此 exit 1
 * （CI Fast run 40664081703 attempt 1，来源
 * src/views/scheduled-tasks/__tests__/scheduledTasksPage.test.ts）。
 *
 * src/test-setup.ts 的全局 afterEach 负责清理这类 timer。本文件用「前一个用例
 * 排 30ms 定时器后立即结束，后一个用例跨过 30ms」验证清理真的发生在用例之间。
 *
 * 变异点：删掉 test-setup.ts 的全局 afterEach（或整个 window.setTimeout 跟踪）
 * → 前一个用例的 timer 会活到后一个用例并触发 spy → 本文件红。
 */
import { describe, expect, it, vi } from 'vitest'

const staleTimerSpy = vi.fn()

describe('test-setup：用例结束时清理仍挂起的真实定时器', () => {
  it('前一个用例：排一个 30ms 定时器后立即结束', () => {
    window.setTimeout(staleTimerSpy, 30)
  })

  it('后一个用例：跨过 30ms 后 spy 未被触发（timer 已被全局 afterEach 清掉）', async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 80))
    expect(staleTimerSpy).not.toHaveBeenCalled()
  })
})
