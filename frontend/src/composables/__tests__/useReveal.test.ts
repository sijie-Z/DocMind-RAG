/**
 * useReveal 判别用例：进场动画必须「可降级」。
 *
 * 修前无此 composable（首屏内容要么硬编码动画要么不动）。本用例锁三条契约：
 *  1) 减少动态偏好 → 立即全部可见，不做动画；
 *  2) 无 IntersectionObserver → 立即全部可见（旧环境不藏内容）；
 *  3) 正常环境 → 进入视口才加 .is-revealed，且只观察一次（unobserve）。
 *
 * 变异点：去掉 reduced/无 IO 的兜底分支 → 用例 1/2 红；去掉 unobserve → 用例 3 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { useReveal } from '@/composables/useReveal'

const Host = defineComponent({
  setup() {
    const root = ref<HTMLElement | null>(null)
    useReveal(root)
    return { root }
  },
  template: '<div ref="root"><span class="reveal">a</span><span class="reveal">b</span></div>',
})

type IOCallback = (entries: Array<{ isIntersecting: boolean; target: Element }>) => void
let ioCallback: IOCallback | null = null
const unobserve = vi.fn()
const observe = vi.fn()
const disconnect = vi.fn()

class FakeIO {
  constructor(cb: IOCallback) {
    ioCallback = cb
  }
  observe = observe
  unobserve = unobserve
  disconnect = disconnect
}

const originalIO = globalThis.IntersectionObserver
const originalMatchMedia = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
  ioCallback = null
})

afterEach(() => {
  globalThis.IntersectionObserver = originalIO
  window.matchMedia = originalMatchMedia
})

function setReduced(reduced: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: reduced }) as unknown as typeof window.matchMedia
}

describe('useReveal 降级与进场契约', () => {
  it('减少动态偏好：立即全部可见', () => {
    setReduced(true)
    globalThis.IntersectionObserver = FakeIO as unknown as typeof IntersectionObserver
    const wrapper = mount(Host, { attachTo: document.body })
    const spans = wrapper.findAll('.reveal')
    expect(spans).toHaveLength(2)
    spans.forEach((s) => expect(s.classes()).toContain('is-revealed'))
    expect(observe).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('无 IntersectionObserver：立即全部可见，不藏内容', () => {
    setReduced(false)
    delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver
    const wrapper = mount(Host, { attachTo: document.body })
    wrapper.findAll('.reveal').forEach((s) => expect(s.classes()).toContain('is-revealed'))
    wrapper.unmount()
  })

  it('正常环境：进入视口才可见，且揭示后停止观察', () => {
    setReduced(false)
    globalThis.IntersectionObserver = FakeIO as unknown as typeof IntersectionObserver
    const wrapper = mount(Host, { attachTo: document.body })
    const spans = wrapper.findAll('.reveal')
    spans.forEach((s) => expect(s.classes()).not.toContain('is-revealed'))
    expect(observe).toHaveBeenCalledTimes(2)

    ioCallback?.([{ isIntersecting: true, target: spans[0].element }])
    expect(spans[0].classes()).toContain('is-revealed')
    expect(spans[1].classes()).not.toContain('is-revealed')
    expect(unobserve).toHaveBeenCalledWith(spans[0].element)

    wrapper.unmount()
    expect(disconnect).toHaveBeenCalled()
  })
})