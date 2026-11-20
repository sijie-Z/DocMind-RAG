/**
 * 条件分支边标签的判别性测试（评审 B 变异 M6/M7）。
 * 变异：normalizeGraphEdge 丢 label / 不传 label → 本文件红。
 */
import { describe, it, expect } from 'vitest'
import { normalizeGraphEdge } from '../composables/useWorkflowEditor'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkflowStore } from '@/stores/workflow'

describe('工作流 · 边标签归一化', () => {
  it('保留 Vue Flow GraphEdge 的字符串 label', () => {
    const edge = normalizeGraphEdge({
      id: 'e1', source: 'cond', target: 'translate-out',
      sourceHandle: 'yes', targetHandle: null,
      label: 'translate', animated: true,
    })
    expect(edge).toEqual({
      id: 'e1', source: 'cond', target: 'translate-out',
      sourceHandle: 'yes', targetHandle: undefined,
      label: 'translate',
    })
  })

  it('VNode label 收敛为 undefined，不抛错', () => {
    const edge = normalizeGraphEdge({ id: 'e2', source: 'a', target: 'b', label: { render: () => null } })
    expect(edge.label).toBeUndefined()
  })

  it('onEdgeClick 归一化后的边进入 store 并可更新 label（store 契约）', () => {
    setActivePinia(createPinia())
    const store = useWorkflowStore()
    const edge = normalizeGraphEdge({ id: 'e3', source: 'cond', target: 'out', label: 'code' })
    store.addEdge(edge)
    store.selectEdge(edge)
    expect(store.selectedEdge?.label).toBe('code')
    store.updateEdge('e3', { label: 'summarize' })
    expect(store.getFlowData().edges[0].label).toBe('summarize')
  })
})
