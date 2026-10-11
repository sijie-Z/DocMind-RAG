/**
 * 体验第四波 运行计时 + 成本可视化 —— RunStats 判别：
 * 运行中秒级计时跳动；终态冻结总用时并显示 tokens 与估算成本（恒带"估算"）；
 * loading 翻转时计时器启停正确。
 *
 * 变异点：elapsedMs 终态不用 endedAt（改用当前时刻）→ 冻结用时用例红；
 * costText 去掉"估算"标注键 → 成本文案用例红。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import RunStats from '@/components/agent/RunStats.vue'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

function mountStats(props: Record<string, unknown>) {
  return mount(RunStats, {
    props,
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'],
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RunStats · 运行中计时', () => {
  it('运行中显示「已运行」秒级跳动（00:00 → 01:05）', async () => {
    vi.setSystemTime(1_000_000)
    const wrapper = mountStats({ loading: true, startedAt: Date.now() })
    expect(wrapper.text()).toContain('已运行 00:00')

    vi.advanceTimersByTime(65_000)
    await nextTick()
    expect(wrapper.text()).toContain('已运行 01:05')
    // 运行中不显示 tokens/成本（未终态）
    expect(wrapper.find('[data-testid="run-stats-tokens"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('loading → false 时计时冻结为总用时，并显示 tokens 与估算成本', async () => {
    vi.setSystemTime(500_000)
    const started = Date.now()
    const wrapper = mountStats({ loading: true, startedAt: started, model: 'deepseek-v4-flash' })

    vi.advanceTimersByTime(10_000)
    await nextTick()
    // 终态落在 +125s
    await wrapper.setProps({ loading: false, endedAt: started + 125_000, tokens: 1234 })
    await nextTick()

    expect(wrapper.text()).toContain('用时 02:05')
    expect(wrapper.find('[data-testid="run-stats-tokens"]').text()).toBe('1,234 Tokens')
    // 1234 token × 0.35/1M ≈ $0.0004（估算）
    const cost = wrapper.find('[data-testid="run-stats-cost"]')
    expect(cost.text()).toContain('$0.0004')
    expect(cost.text()).toContain('估算')

    // 冻结：再推进时间不改变显示
    vi.advanceTimersByTime(60_000)
    await nextTick()
    expect(wrapper.text()).toContain('用时 02:05')
    wrapper.unmount()
  })

  it('无 tokens → 不显示 tokens/成本（负例）；无 endedAt 的终态按当前时刻冻结', async () => {
    vi.setSystemTime(200_000)
    const wrapper = mountStats({ loading: false, startedAt: 200_000 - 30_000, model: 'deepseek-v4-flash' })
    expect(wrapper.text()).toContain('用时 00:30')
    expect(wrapper.find('[data-testid="run-stats-tokens"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="run-stats-cost"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
