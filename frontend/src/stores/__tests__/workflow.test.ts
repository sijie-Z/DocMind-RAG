import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWorkflowStore, type WorkflowNode, type WorkflowEdge } from '../workflow'

const makeNode = (id: string, type = 'input', label = 'Node'): WorkflowNode => ({
  id,
  type,
  position: { x: 100, y: 200 },
  data: { label, type },
})

const makeEdge = (id: string, source: string, target: string): WorkflowEdge => ({
  id,
  source,
  target,
})

describe('useWorkflowStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  // ── State ──────────────────────────────────────────────

  it('has correct initial state', () => {
    const store = useWorkflowStore()
    expect(store.currentWorkflowId).toBeNull()
    expect(store.workflowName).toBe('')
    expect(store.nodes).toEqual([])
    expect(store.edges).toEqual([])
    expect(store.selectedNode).toBeNull()
    expect(store.isExecuting).toBe(false)
  })

  // ── Node CRUD ──────────────────────────────────────────

  it('setNodes replaces all nodes', () => {
    const store = useWorkflowStore()
    const nodes = [makeNode('a'), makeNode('b')]
    store.setNodes(nodes)
    expect(store.nodes).toHaveLength(2)
  })

  it('addNode appends a node', () => {
    const store = useWorkflowStore()
    store.addNode(makeNode('a'))
    store.addNode(makeNode('b'))
    expect(store.nodes).toHaveLength(2)
    expect(store.nodes[1].id).toBe('b')
  })

  it('removeNode removes node and connected edges', () => {
    const store = useWorkflowStore()
    store.setNodes([makeNode('a'), makeNode('b'), makeNode('c')])
    store.setEdges([makeEdge('e1', 'a', 'b'), makeEdge('e2', 'b', 'c')])
    store.removeNode('b')
    expect(store.nodes).toHaveLength(2)
    expect(store.edges).toHaveLength(0)
  })

  // ── Edge CRUD ──────────────────────────────────────────

  it('setEdges replaces all edges', () => {
    const store = useWorkflowStore()
    store.setEdges([makeEdge('e1', 'a', 'b')])
    expect(store.edges).toHaveLength(1)
  })

  it('addEdge appends an edge', () => {
    const store = useWorkflowStore()
    store.addEdge(makeEdge('e1', 'a', 'b'))
    store.addEdge(makeEdge('e2', 'b', 'c'))
    expect(store.edges).toHaveLength(2)
  })

  it('removeEdge removes a specific edge', () => {
    const store = useWorkflowStore()
    store.setEdges([makeEdge('e1', 'a', 'b'), makeEdge('e2', 'b', 'c')])
    store.removeEdge('e1')
    expect(store.edges).toHaveLength(1)
    expect(store.edges[0].id).toBe('e2')
  })

  // ── Selection ──────────────────────────────────────────

  it('selectNode sets selectedNode', () => {
    const store = useWorkflowStore()
    const node = makeNode('a')
    store.selectNode(node)
    expect(store.selectedNode).toEqual(node)
  })

  it('selectNode(null) clears selection', () => {
    const store = useWorkflowStore()
    store.selectNode(makeNode('a'))
    store.selectNode(null)
    expect(store.selectedNode).toBeNull()
  })

  // ── updateNodeData ─────────────────────────────────────

  it('updateNodeData merges data into existing node', () => {
    const store = useWorkflowStore()
    store.addNode(makeNode('a', 'llm', 'LLM'))
    store.updateNodeData('a', { temperature: 0.9 })
    expect(store.nodes[0].data.temperature).toBe(0.9)
    expect(store.nodes[0].data.label).toBe('LLM')
  })

  it('updateNodeData does nothing for non-existent node', () => {
    const store = useWorkflowStore()
    store.addNode(makeNode('a'))
    store.updateNodeData('nonexistent', { x: 1 })
    expect(store.nodes).toHaveLength(1)
  })

  // ── clearWorkflow ──────────────────────────────────────

  it('clearWorkflow resets all state', () => {
    const store = useWorkflowStore()
    store.addNode(makeNode('a'))
    store.addEdge(makeEdge('e1', 'a', 'b'))
    store.selectNode(makeNode('a'))
    store.workflowName = 'Test'
    store.currentWorkflowId = 42

    store.clearWorkflow()
    expect(store.nodes).toEqual([])
    expect(store.edges).toEqual([])
    expect(store.selectedNode).toBeNull()
    expect(store.currentWorkflowId).toBeNull()
    expect(store.workflowName).toBe('')
  })

  // ── loadWorkflow ───────────────────────────────────────

  it('loadWorkflow populates state from workflow object', () => {
    const store = useWorkflowStore()
    const nodes = [makeNode('a'), makeNode('b')]
    const edges = [makeEdge('e1', 'a', 'b')]
    store.loadWorkflow({ id: 10, name: 'My Flow', description: 'desc', flow_data: { nodes, edges } })
    expect(store.currentWorkflowId).toBe(10)
    expect(store.workflowName).toBe('My Flow')
    expect(store.workflowDescription).toBe('desc')
    expect(store.nodes).toHaveLength(2)
    expect(store.edges).toHaveLength(1)
  })

  it('loadWorkflow with no flow_data leaves nodes/edges empty', () => {
    const store = useWorkflowStore()
    store.loadWorkflow({ id: 1, name: 'Empty' })
    expect(store.nodes).toEqual([])
    expect(store.edges).toEqual([])
  })

  // ── Execution ──────────────────────────────────────────

  it('startExecution sets isExecuting and clears results', () => {
    const store = useWorkflowStore()
    store.startExecution()
    expect(store.isExecuting).toBe(true)
    expect(store.executionResults).toEqual([])
    expect(store.executionLogs).toEqual([])
  })

  it('addExecutionLog appends a log entry', () => {
    const store = useWorkflowStore()
    store.addExecutionLog('test message')
    expect(store.executionLogs).toHaveLength(1)
    expect(store.executionLogs[0]).toContain('test message')
  })

  it('updateNodeExecution adds new result', () => {
    const store = useWorkflowStore()
    store.updateNodeExecution({ nodeId: 'n1', nodeType: 'llm', status: 'success' })
    expect(store.executionResults).toHaveLength(1)
    expect(store.executionResults[0].status).toBe('success')
  })

  it('updateNodeExecution updates existing result', () => {
    const store = useWorkflowStore()
    store.updateNodeExecution({ nodeId: 'n1', nodeType: 'llm', status: 'running' })
    store.updateNodeExecution({ nodeId: 'n1', nodeType: 'llm', status: 'success', duration: 100 })
    expect(store.executionResults).toHaveLength(1)
    expect(store.executionResults[0].status).toBe('success')
    expect(store.executionResults[0].duration).toBe(100)
  })

  it('setExecutionComplete stops execution and sets output', () => {
    const store = useWorkflowStore()
    store.startExecution()
    store.setExecutionComplete({ result: 'done' })
    expect(store.isExecuting).toBe(false)
    expect(store.finalOutput).toEqual({ result: 'done' })
  })

  it('resetExecution clears all execution state', () => {
    const store = useWorkflowStore()
    store.startExecution()
    store.addExecutionLog('log')
    store.setExecutionComplete({ x: 1 })
    store.resetExecution()
    expect(store.isExecuting).toBe(false)
    expect(store.executionResults).toEqual([])
    expect(store.executionLogs).toEqual([])
    expect(store.finalOutput).toBeNull()
  })

  // ── getFlowData ────────────────────────────────────────

  it('getFlowData returns serialized nodes and edges', () => {
    const store = useWorkflowStore()
    store.addNode(makeNode('a', 'input', 'Input'))
    store.addEdge(makeEdge('e1', 'a', 'b'))
    const flowData = store.getFlowData()
    expect(flowData.nodes).toHaveLength(1)
    expect(flowData.edges).toHaveLength(1)
    expect(flowData.nodes[0].id).toBe('a')
    expect(flowData.edges[0].source).toBe('a')
  })

  it('getFlowData 保留边 label（条件路由键，B12）；不携带后端将剥离的 animated（B13）', () => {
    const store = useWorkflowStore()
    store.setEdges([{ ...makeEdge('e1', 'a', 'b'), label: 'yes' }])
    const flowData = store.getFlowData()
    // label：WorkflowEdge schema 唯一保留的附加字段（schemas/workflow.py:28），
    // 引擎条件路由读 edge.label（workflow_engine.py:886-890）——漏发则静默错路由
    expect(flowData.edges[0].label).toBe('yes')
    // animated：后端 WorkflowEdge 无该字段（无 extra=allow，保存即剥离），
    // 渲染侧由 VueFlow defaultEdgeOptions 兜底 —— 序列化不再伪装可持久化
    expect('animated' in flowData.edges[0]).toBe(false)
  })
})
