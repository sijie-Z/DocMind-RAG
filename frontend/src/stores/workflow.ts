import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { WorkflowNode, WorkflowEdge } from '@/api/workflow'

export type { WorkflowNode, WorkflowEdge }

export interface NodeExecutionResult {
  nodeId: string
  nodeType: string
  // 与引擎 ExecutionStatus 对齐（workflow_engine.py:37-42）：成功态为 completed。
  // 旧词表 'success' 使成功节点在进度/图标/文案处恒判失败（B10）。
  status: 'pending' | 'running' | 'completed' | 'failed'
  input?: Record<string, unknown>
  output?: Record<string, unknown>
  error?: string
  duration?: number
  startedAt?: string
  completedAt?: string
}

export const useWorkflowStore = defineStore('workflow', () => {
  // 当前工作流
  const currentWorkflowId = ref<number | null>(null)
  const workflowName = ref('')
  const workflowDescription = ref('')

  // 节点和边
  const nodes = ref<WorkflowNode[]>([])
  const edges = ref<WorkflowEdge[]>([])

  // 选中的节点 / 边（条件分支 label 编辑入口）
  const selectedNode = ref<WorkflowNode | null>(null)
  const selectedEdge = ref<WorkflowEdge | null>(null)

  // 执行状态
  const isExecuting = ref(false)
  const executionResults = ref<NodeExecutionResult[]>([])
  const executionLogs = ref<string[]>([])
  const finalOutput = ref<Record<string, unknown> | string | null>(null)

  // Actions
  const setNodes = (newNodes: WorkflowNode[]) => {
    nodes.value = newNodes
  }

  const setEdges = (newEdges: WorkflowEdge[]) => {
    edges.value = newEdges
  }

  const addNode = (node: WorkflowNode) => {
    nodes.value.push(node)
  }

  const removeNode = (nodeId: string) => {
    nodes.value = nodes.value.filter(n => n.id !== nodeId)
    edges.value = edges.value.filter(e => e.source !== nodeId && e.target !== nodeId)
  }

  const addEdge = (edge: WorkflowEdge) => {
    edges.value.push(edge)
  }

  const removeEdge = (edgeId: string) => {
    edges.value = edges.value.filter(e => e.id !== edgeId)
  }

  const selectNode = (node: WorkflowNode | null) => {
    selectedNode.value = node
    if (node) selectedEdge.value = null
  }

  const selectEdge = (edge: WorkflowEdge | null) => {
    selectedEdge.value = edge
    if (edge) selectedNode.value = null
  }

  const updateEdge = (edgeId: string, patch: Partial<WorkflowEdge>) => {
    const edge = edges.value.find(e => e.id === edgeId)
    if (!edge) return
    Object.assign(edge, patch)
    if (selectedEdge.value?.id === edgeId) Object.assign(selectedEdge.value, patch)
  }

  const updateNodeData = (nodeId: string, data: Partial<WorkflowNode['data']>) => {
    const node = nodes.value.find(n => n.id === nodeId)
    if (node) {
      node.data = { ...node.data, ...data }
    }
  }

  const clearWorkflow = () => {
    nodes.value = []
    edges.value = []
    selectedNode.value = null
    currentWorkflowId.value = null
    workflowName.value = ''
    workflowDescription.value = ''
  }

  const loadWorkflow = (workflow: { id: number; name: string; description?: string; flow_data?: { nodes: WorkflowNode[]; edges: WorkflowEdge[] } }) => {
    currentWorkflowId.value = workflow.id
    workflowName.value = workflow.name
    workflowDescription.value = workflow.description || ''
    if (workflow.flow_data) {
      nodes.value = workflow.flow_data.nodes || []
      edges.value = workflow.flow_data.edges || []
    }
  }

  // 执行相关
  const startExecution = () => {
    isExecuting.value = true
    executionResults.value = []
    executionLogs.value = []
    finalOutput.value = null
  }

  const addExecutionLog = (log: string) => {
    const timestamp = new Date().toLocaleTimeString()
    executionLogs.value.push(`[${timestamp}] ${log}`)
  }

  const updateNodeExecution = (result: NodeExecutionResult) => {
    const index = executionResults.value.findIndex(r => r.nodeId === result.nodeId)
    if (index >= 0) {
      executionResults.value[index] = result
    } else {
      executionResults.value.push(result)
    }
  }

  const setExecutionComplete = (output: Record<string, unknown> | string | null) => {
    isExecuting.value = false
    finalOutput.value = output
  }

  /** 整次执行失败：写入一条失败结果，让 executionStatus 判为 failed。 */
  const markExecutionFailed = (error: string) => {
    isExecuting.value = false
    finalOutput.value = null
    updateNodeExecution({
      nodeId: '__workflow__',
      nodeType: 'workflow',
      status: 'failed',
      error,
    })
  }

  const resetExecution = () => {
    isExecuting.value = false
    executionResults.value = []
    executionLogs.value = []
    finalOutput.value = null
  }

  // 获取 flowData 用于保存
  const getFlowData = () => ({
    nodes: nodes.value.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
    // 契约对齐（B12/B13）：后端 WorkflowEdge 仅保留 id/source/target/sourceHandle/
    // targetHandle/label（schemas/workflow.py:21-28）。
    //  - label 是条件分支路由键（workflow_engine.py:886-890 读 edge.label），旧序列化
    //    漏发 → 多目标条件边在引擎侧恒回落第一条（静默错路由）——补上；
    //  - animated/markerEnd 后端无字段（保存即剥离），渲染侧由 VueFlow
    //    defaultEdgeOptions 统一兜底（editor.vue :default-edge-options，
    //    vue-flow parseEdge 对全部边合并默认项），不再进入持久化载荷伪装“已保存”。
    edges: edges.value.map(e => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle, label: e.label }))
  })

  return {
    // State
    currentWorkflowId,
    workflowName,
    workflowDescription,
    nodes,
    edges,
    selectedNode,
    selectedEdge,
    isExecuting,
    executionResults,
    executionLogs,
    finalOutput,

    // Actions
    setNodes,
    setEdges,
    addNode,
    removeNode,
    addEdge,
    removeEdge,
    selectNode,
    selectEdge,
    updateEdge,
    updateNodeData,
    clearWorkflow,
    loadWorkflow,
    startExecution,
    addExecutionLog,
    updateNodeExecution,
    setExecutionComplete,
    markExecutionFailed,
    resetExecution,
    getFlowData
  }
})