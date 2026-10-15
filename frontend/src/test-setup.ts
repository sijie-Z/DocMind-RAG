/**
 * vitest 的全局测试环境准备。
 *
 * 存在的理由：jsdom 实现了大部分浏览器 API，但**故意没实现**少数几个 ——
 * `window.matchMedia` 是其中最常撞上的一个。它在生产代码里被合法使用
 * （`src/stores/app.ts:15` 用它判断系统深色模式偏好），但在 jsdom 下是
 * `undefined`，于是任何 import 该 store 的测试都会在 setup 阶段抛异常：
 *
 *     TypeError: window.matchMedia is not a function
 *
 * 这是**测试环境的缺口，不是产品缺陷** —— 所以修在测试侧，不动产品代码。
 */
import { vi } from 'vitest'

if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      // 下面四个是 MediaQueryList 的历史/现代两套订阅接口，jsdom 下都要补齐，
      // 否则调用方（VueUse、组件库等）会拿到 undefined 再炸一次。
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }),
  })
}

// ResizeObserver 同属 jsdom 缺口（NDataTable/NCollapse 等组件挂载即用），
// 测试侧补最小桩：observe/unobserve/disconnect 全空操作即可（无布局断言需求）。
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub
}
