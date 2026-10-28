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
import { afterEach, vi } from 'vitest'

// AgentInput 挂载即 GET /commands（issue #475）。未显式 mock 该模块的测试文件
// 不应触发真实 XHR：jsdom 下请求会卡在 axios 超时/重试链上，拖垮整个套件。
// 默认给空清单（菜单只余本地模板）；需要断言命令行为的测试文件用各自的
// vi.mock('@/api/commands', …) 覆盖本默认。
vi.mock('@/api/commands', () => ({
  commandsApi: {
    list: vi.fn().mockResolvedValue({
      data: {
        success: true,
        data: { enabled: true, scope: 'global', commands: [] },
      },
    }),
    execute: vi.fn(),
  },
}))

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

/**
 * 清理测试结束时仍挂起的真实定时器（naive-ui 消息/通知的自动消失 timer）。
 *
 * naive-ui 2.45.3 的 MessageEnvironment 在消息挂载时用 `window.setTimeout(hide,
 * duration)` 排一个默认 3s 的自动隐藏定时器，但环境组件**没有卸载清理**
 * （MessageEnvironment.js 的 setup 里只有 onMounted，没有 onUnmounted）。
 * 用例结束、wrapper 卸载、vitest 拆除 jsdom 之后，该 timer 才到期；回调
 * `hide()` 执行到 `window.clearTimeout(timerId)` 时抛
 * `ReferenceError: window is not defined`。vitest 把它记成 unhandled error，
 * 整个 Frontend Tests job 因此 exit 1 —— 即使全部测试通过。
 *
 * 实例：CI Fast run 40664081703（develop @ a83a2ce5）attempt 1，199 个测试文件
 * 全绿后报错，来源标注为
 * src/views/scheduled-tasks/__tests__/scheduledTasksPage.test.ts。
 *
 * 修法放在全局 setup 而非单个测试文件：跟踪测试期间经 `window.setTimeout`
 * 创建、到测试结束时仍未触发的 timer，在全局 afterEach 里统一 clear。setup
 * 文件的 hook 先注册，按 vitest 的 stack 语义最后执行，因此落在各测试文件
 * 自己的 unmount 之后、jsdom 拆除之前。只清理「仍未触发」的 timer：测试体内
 * 正常执行的定时器不受影响，也不会吞掉测试期间抛出的真实错误（那些错误仍会
 * 让对应用例失败）。
 */
const realSetTimeout = window.setTimeout.bind(window) as unknown as (
  handler: TimerHandler,
  timeout?: number,
  ...args: unknown[]
) => number
const realClearTimeout = window.clearTimeout.bind(window) as (timerId?: number) => void
const pendingTimeouts = new Set<number>()

window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
  const timerId = realSetTimeout(handler, timeout, ...args)
  pendingTimeouts.add(timerId)
  return timerId
}) as typeof window.setTimeout

afterEach(() => {
  for (const timerId of pendingTimeouts) realClearTimeout(timerId)
  pendingTimeouts.clear()
})
