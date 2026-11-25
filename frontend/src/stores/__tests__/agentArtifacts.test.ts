import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '../agent'

/**
 * 批次七：artifact 事件在 store 的落账（时间线留痕 + 工件右栏刷新键）。
 *
 * 变异点：删 stores/agent.ts 的 `case 'artifact'` → 两条用例红
 * （事件不进时间线、右栏永远不刷新）；刷新键不在 identity reset 时递增 →
 * 第三条用例红（换账号后旧事件仍触发新账号重拉）。
 */
describe('useAgentStore · artifact 事件（批次七）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function seedAssistantMessage() {
    const store = useAgentStore()
    store.addMessage({
      role: 'assistant',
      content: '',
      events: [],
      loading: true,
      currentTool: '',
      thinkingText: '',
      planSteps: [],
      planId: '',
      progress: 0,
    })
    return store
  }

  it('artifact 事件进入最后一条 assistant 消息的事件台账', () => {
    const store = seedAssistantMessage()
    store.processEvent({
      type: 'artifact',
      content: '季度报告',
      artifactId: 'art-1',
      title: '季度报告',
      kind: 'markdown',
      version: 1,
      origin: 'tool',
      contentUrl: '/api/v1/artifacts/art-1/content',
    } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.events).toHaveLength(1)
    expect(msg.events[0].type).toBe('artifact')
    expect(msg.events[0].artifactId).toBe('art-1')
  })

  it('每次 artifact 事件递增刷新键（右栏 watcher 的数据源）', () => {
    const store = seedAssistantMessage()
    const before = store.artifactRefreshKey
    store.processEvent({ type: 'artifact', content: 'A', artifactId: 'a1' } as any)
    store.processEvent({ type: 'artifact', content: 'B', artifactId: 'a2' } as any)
    expect(store.artifactRefreshKey).toBe(before + 2)
  })

  it('身份切换（登出/换账号）同样递增刷新键', () => {
    const store = seedAssistantMessage()
    const before = store.artifactRefreshKey
    store.resetForIdentityChange()
    expect(store.artifactRefreshKey).toBe(before + 1)
  })
})
