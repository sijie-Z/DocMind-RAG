// Workflow editor composable — extracted from views/workflow/editor.vue

import { ref, computed, onMounted, watch, onUnmounted, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow, MarkerType, type Connection } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import {
  NIcon, NInput, NButton, NInputNumber, NCard, NCollapse, NCollapseItem,
  NDivider, NDrawer, NDrawerContent, NTimeline, NTimelineItem, NTag, NSpin, NProgress,
  NModal, NForm, NGrid, NFormItem, NSelect, NSwitch, NList, NListItem, NEmpty, NPopconfirm, NDataTable, NPopover,
  type DataTableColumns
} from 'naive-ui'
import {
  SaveOutline, PlayOutline, TrashOutline, ExpandOutline, SettingsOutline,
  AppsOutline, LocateOutline, CheckmarkCircleOutline, CloseCircleOutline,
  HardwareChipOutline, ChatbubbleEllipsesOutline, SearchOutline, VolumeHighOutline,
  EnterOutline, ExitOutline, GitBranchOutline, CompassOutline, ServerOutline,
  CodeSlashOutline, GlobeOutline, SyncOutline, RocketOutline, FlashOutline,
  FolderOpenOutline, AddOutline, CloseOutline
} from '@vicons/ionicons5'
import { useWorkflowStore } from '@/stores/workflow'
import { useLLMConfigStore, type LLMProviderConfig } from '@/stores/llmConfigStore'
import { getWorkflow, createWorkflow, updateWorkflow, executeWorkflow as execWorkflow, getWorkflows, getNodeDefinitions, type WorkflowNode, type WorkflowEdge, type WorkflowConfig, type NodeDefinition } from '@/api/workflow'
import { agentApi } from '@/api/agent'
import type { SkillInfo } from '@/types/agent'
import { useDedupedMessage } from '@/utils/message'
import InputNode from '../nodes/InputNode.vue'
import OutputNode from '../nodes/OutputNode.vue'
import LLMNode from '../nodes/LLMNode.vue'
import ToolNode from '../nodes/ToolNode.vue'
import ConditionNode from '../nodes/ConditionNode.vue'
import MemoryNode from '../nodes/MemoryNode.vue'
import CodeNode from '../nodes/CodeNode.vue'
import ApiNode from '../nodes/ApiNode.vue'
import TransformNode from '../nodes/TransformNode.vue'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const route = useRoute()
const router = useRouter()
const message = useDedupedMessage()
const workflowStore = useWorkflowStore()
const llmConfigStore = useLLMConfigStore()

const { fitView: doFitView } = useVueFlow()

// 边的默认配置
const defaultEdgeOptions = {
  animated: true,
  style: { strokeWidth: 2, stroke: '#94a3b8' },
  markerEnd: MarkerType.ArrowClosed,
  type: 'smoothstep'
}

// ── 从 API 动态加载的节点定义 ──
const nodeDefinitions = ref<NodeDefinition[]>([])
const nodeDefsLoaded = ref(false)
const nodeDefsByCategory = computed(() => {
  const groups: Record<string, NodeDefinition[]> = {}
  for (const def of nodeDefinitions.value) {
    const cat = def.category || 'other'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(def)
  }
  return groups
})

const CATEGORY_LABELS: Record<string, string> = {
  llm: '大模型节点',
  tool: '工具节点',
  io: '输入输出',
  logic: '逻辑控制',
  data: '数据处理',
}
const CATEGORY_ICONS: Record<string, unknown> = {
  llm: HardwareChipOutline,
  tool: SettingsOutline,
  io: EnterOutline,
  logic: GitBranchOutline,
  data: ServerOutline,
}

// ── 硬编码回退（API 加载失败时使用）──
const FALLBACK_NODES: NodeDefinition[] = [
  { id: 0, node_type: 'llm', name: '通用LLM', category: 'llm', description: '支持多供应商的通用大模型节点' },
  { id: 0, node_type: 'llm_openai', name: 'OpenAI GPT', category: 'llm', description: 'GPT-4o 等模型' },
  { id: 0, node_type: 'llm_deepseek', name: 'DeepSeek', category: 'llm', description: '国产推理模型' },
  { id: 0, node_type: 'llm_qwen', name: '通义千问', category: 'llm', description: '阿里云大模型' },
  { id: 0, node_type: 'tool_search', name: '知识库检索', category: 'tool', description: 'RAG检索增强' },
  { id: 0, node_type: 'tool_tts', name: '语音合成', category: 'tool', description: '文本转语音' },
  { id: 0, node_type: 'input', name: '输入节点', category: 'io', description: '工作流入口' },
  { id: 0, node_type: 'output', name: '输出节点', category: 'io', description: '工作流出口' },
  { id: 0, node_type: 'condition', name: '条件分支', category: 'logic', description: '根据条件路由' },
  { id: 0, node_type: 'router', name: '智能路由', category: 'logic', description: 'LLM智能路由' },
  { id: 0, node_type: 'memory', name: '记忆节点', category: 'data', description: '存储/检索记忆' },
  { id: 0, node_type: 'code', name: '代码执行', category: 'data', description: '执行Python代码' },
  { id: 0, node_type: 'api_call', name: 'API调用', category: 'data', description: 'HTTP请求' },
  { id: 0, node_type: 'transform', name: '数据转换', category: 'data', description: 'JSON/文本处理' },
]

// 在 template 中使用的动态节点列表（回退到硬编码）
const llmNodes = computed(() => nodeDefsByCategory.value['llm'] || [])
const toolNodes = computed(() => nodeDefsByCategory.value['tool'] || [])
const ioNodes = computed(() => nodeDefsByCategory.value['io'] || [])
const logicNodes = computed(() => nodeDefsByCategory.value['logic'] || [])
const dataNodes = computed(() => nodeDefsByCategory.value['data'] || [])
const hasDynamicDefs = computed(() => nodeDefsLoaded.value && nodeDefinitions.value.length > 0)

// 配置选项
const openaiModels = [
  { label: 'GPT-4o', value: 'gpt-4o' },
  { label: 'GPT-4o Mini', value: 'gpt-4o-mini' },
  { label: 'GPT-4 Turbo', value: 'gpt-4-turbo' }
]

const deepseekModels = [
  { label: 'DeepSeek V4 Flash', value: 'deepseek-v4-flash' },
  { label: 'DeepSeek V4 Pro', value: 'deepseek-v4-pro' }
]

const qwenModels = [
  { label: '通义千问 Plus', value: 'qwen-plus' },
  { label: '通义千问 Max', value: 'qwen-max' }
]

const memoryTypeOptions = [
  { label: '短期记忆', value: 'short_term' },
  { label: '长期记忆', value: 'long_term' }
]

const memoryActionOptions = [
  { label: '存储', value: 'store' },
  { label: '检索', value: 'retrieve' }
]

const httpMethods = [
  { label: 'GET', value: 'GET' },
  { label: 'POST', value: 'POST' },
  { label: 'PUT', value: 'PUT' },
  { label: 'DELETE', value: 'DELETE' }
]

const codeLanguages = [
  { label: 'Python', value: 'python' },
  { label: 'JavaScript', value: 'javascript' }
]

const transformTypes = [
  { label: 'JSON提取', value: 'json_extract' },
  { label: '文本截取', value: 'text_slice' },
  { label: '正则提取', value: 'regex_extract' }
]

// ── Skills 列表 ──
const skills = ref<SkillInfo[]>([])
const skillsLoaded = ref(false)

async function loadSkills() {
  if (skillsLoaded.value) return
  try {
    const res = await agentApi.listSkills()
    skills.value = (res as any).data?.data || (res as any).data || []
    skillsLoaded.value = true
  } catch {
    // 静默失败，skills 是可选功能
  }
}

// ── 参数定义接口（Input/LLM/Output 节点共用）──
interface ParamDef {
  name: string
  type: 'input' | 'reference'
  value: string
  referenceNode?: string
}

// 状态
const saving = ref(false)
const executing = ref(false)
const executingNodeId = ref<string | null>(null)
const showDebugPanel = ref(false)
const showLLMConfig = ref(false)
const showLoadModal = ref(false)
const workflowList = ref<{ id: number; name: string; created_at: string }[]>([])
const loadingWorkflows = ref(false)
const testInput = ref('')
const vueFlowRef = ref()
const engineType = ref<'dag' | 'langgraph'>('dag')
const engineTypeOptions = [
  { label: 'DAG 引擎', value: 'dag' as const },
  { label: 'LangGraph 引擎', value: 'langgraph' as const },
]

// LLM 全局配置（表格式管理）
const editingConfigId = ref<string | null>(null)

const llmConfigForm = ref({
  provider: 'deepseek' as string,
  config_name: '',
  api_key: '',
  api_url: '',
  model: '',
  temperature: 0.7,
  is_default: false,
})

function resetLlmConfigForm() {
  editingConfigId.value = null
  llmConfigForm.value = {
    provider: 'deepseek',
    config_name: '',
    api_key: '',
    api_url: '',
    model: '',
    temperature: 0.7,
    is_default: false,
  }
}

// 表格列定义
const llmConfigColumns: DataTableColumns<LLMProviderConfig> = [
  { title: '供应商', key: 'provider', width: 90, render: (row) => llmConfigStore.getProviderLabel(row.provider) },
  { title: '配置名', key: 'config_name', ellipsis: { tooltip: true }, width: 130 },
  { title: 'API URL', key: 'api_url', ellipsis: { tooltip: true }, width: 160 },
  { title: '模型', key: 'model', width: 120 },
  { title: '温度', key: 'temperature', width: 60 },
  {
    title: '默认', key: 'is_default', width: 60,
    render: (row) => row.is_default ? h('span', { style: 'color: #10b981' }, '✓') : ''
  },
  {
    title: '操作', key: 'actions', width: 140,
    render: (row) => {
      const buttons: any[] = [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => editConfig(row) }, { icon: () => h(NIcon, null, h(SearchOutline as any)) }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => handleDeleteConfigById(row.id) }, { icon: () => h(NIcon, null, h(TrashOutline as any)) }),
      ]
      if (!row.is_default) {
        buttons.splice(1, 0,
          h(NButton, { size: 'tiny', quaternary: true, onClick: () => handleSetDefault(row.id) }, { default: () => '设默认' })
        )
      }
      return h('div', { style: 'display:flex;gap:4px' }, buttons)
    },
  },
]

function editConfig(config: LLMProviderConfig) {
  editingConfigId.value = config.id
  llmConfigForm.value = {
    provider: config.provider,
    config_name: config.config_name,
    api_key: config.api_key,
    api_url: config.api_url,
    model: config.model,
    temperature: config.temperature,
    is_default: config.is_default,
  }
}

async function handleSaveConfig() {
  if (!llmConfigForm.value.config_name.trim()) {
    message.error('请输入配置名称')
    return
  }
  if (!llmConfigForm.value.api_key.trim()) {
    message.error('请输入 API Key')
    return
  }
  try {
    if (editingConfigId.value) {
      await llmConfigStore.update(editingConfigId.value, llmConfigForm.value as any)
      message.success('配置已更新')
    } else {
      await llmConfigStore.create(llmConfigForm.value as any)
      message.success('配置已创建')
    }
    resetLlmConfigForm()
  } catch {
    message.error('保存失败')
  }
}

async function handleDeleteConfigById(configId: string) {
  try {
    await llmConfigStore.remove(configId)
    message.success('配置已删除')
    if (editingConfigId.value === configId) resetLlmConfigForm()
  } catch {
    message.error('删除失败')
  }
}

async function handleDeleteConfig() {
  if (editingConfigId.value) await handleDeleteConfigById(editingConfigId.value)
}

async function handleSetDefault(configId: string) {
  try {
    await llmConfigStore.setDefault(configId)
    message.success('已设为默认')
  } catch {
    message.error('设置失败')
  }
}

// ── 自动保存定时器 ──
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

// 本地状态
const nodes = computed({
  get: () => workflowStore.nodes as any,
  set: (val) => workflowStore.setNodes(val)
})

const edges = computed({
  get: () => workflowStore.edges as any,
  set: (val) => workflowStore.setEdges(val)
})

const selectedNodeId = computed(() => workflowStore.selectedNode?.id)
const selectedNode = computed(() => workflowStore.selectedNode)
const nodeData = computed(() => (selectedNode.value?.data ?? {}) as Record<string, any>)
const executionResults = computed(() => workflowStore.executionResults)
const executionLogs = computed(() => workflowStore.executionLogs)
const finalOutput = computed(() => workflowStore.finalOutput)

const executionStatus = computed(() => {
  if (executionResults.value.length === 0) return 'idle'
  if (executionResults.value.some(r => r.status === 'failed')) return 'failed'
  if (executionResults.value.every(r => r.status === 'success')) return 'success'
  return 'running'
})

const executionProgress = computed(() => {
  if (executionResults.value.length === 0) return 0
  const completed = executionResults.value.filter(r => r.status === 'success').length
  return Math.round((completed / executionResults.value.length) * 100)
})

const completedNodes = computed(() => executionResults.value.filter(r => r.status === 'success').length)

const statusText = computed(() => {
  const map: Record<string, string> = {
    idle: '等待执行',
    running: '执行中',
    success: '执行成功',
    failed: '执行失败'
  }
  return map[executionStatus.value] || '未知'
})

const nodeColor = (node: { type?: string }) => {
  const colorMap: Record<string, string> = {
    input: '#3b82f6',
    output: '#10b981',
    llm_openai: '#22c55e',
    llm_deepseek: '#3b82f6',
    llm_qwen: '#f97316',
    tool_search: '#f59e0b',
    condition: '#06b6d4',
    memory: '#3b82f6',
    code: '#f43f5e',
    api_call: '#0ea5e9',
    transform: '#14b8a6'
  }
  return colorMap[node.type ?? ''] || '#6b7280'
}

// 模板加载
const loadTemplate = (type: string) => {
  workflowStore.clearWorkflow()

  const templates: Record<string, { name: string; nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] }> = {
    rag: {
      name: 'RAG 问答流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'search_1', type: 'tool_search', position: { x: 300, y: 100 }, data: { topK: 5, scoreThreshold: 0.5, label: '知识库检索' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 500, y: 200 }, data: { systemPrompt: '基于参考资料回答用户问题，如果资料中没有相关信息请如实说明', temperature: 0.7, maxTokens: 2048, label: 'DeepSeek' } },
        { id: 'output_1', type: 'output', position: { x: 700, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'search_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'search_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    chat: {
      name: '多轮对话流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'llm_1', type: 'llm_openai', position: { x: 350, y: 200 }, data: { systemPrompt: '你是一个有帮助的AI助手，请友好、专业地回答用户的问题。', temperature: 0.7, maxTokens: 2048, label: 'OpenAI GPT' } },
        { id: 'output_1', type: 'output', position: { x: 600, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'llm_1', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    agent: {
      name: 'Agent 记忆流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'memory_1', type: 'memory', position: { x: 300, y: 100 }, data: { memoryType: 'short_term', action: 'retrieve', label: '检索记忆' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 500, y: 200 }, data: { systemPrompt: '你是一个有记忆的AI助手，请结合历史记忆回答问题。', temperature: 0.7, maxTokens: 2048, label: 'DeepSeek' } },
        { id: 'memory_2', type: 'memory', position: { x: 700, y: 100 }, data: { memoryType: 'short_term', action: 'store', label: '存储记忆' } },
        { id: 'output_1', type: 'output', position: { x: 900, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'memory_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'memory_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'memory_2', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e4', source: 'memory_2', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    report: {
      name: '报告生成流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 50, y: 200 }, data: { prompt: '请输入报告主题', label: '输入' } },
        { id: 'search_1', type: 'tool_search', position: { x: 200, y: 100 }, data: { topK: 10, scoreThreshold: 0.4, label: '资料检索' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 400, y: 100 }, data: { systemPrompt: '基于检索到的资料，生成一份结构清晰的报告大纲。', temperature: 0.5, maxTokens: 1000, label: '生成大纲' } },
        { id: 'llm_2', type: 'llm_deepseek', position: { x: 600, y: 200 }, data: { systemPrompt: '根据大纲和资料，撰写完整的报告内容。', temperature: 0.7, maxTokens: 4000, label: '撰写报告' } },
        { id: 'output_1', type: 'output', position: { x: 800, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'search_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'search_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'llm_2', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e4', source: 'llm_2', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    }
  }

  const template = templates[type]
  if (template) {
    workflowStore.workflowName = template.name
    workflowStore.setNodes(template.nodes as unknown as WorkflowNode[])
    workflowStore.setEdges(template.edges as unknown as WorkflowEdge[])
    message.success('模板加载成功')
  }
}

// 工具函数
const getNodeLabel = (type: string) => {
  // 优先从动态加载的节点定义中查找
  const def = nodeDefinitions.value.find(d => d.node_type === type)
  if (def) return def.name
  const defs = FALLBACK_NODES.find(d => d.node_type === type)
  if (defs) return defs.name
  return type
}

const formatOutput = (output: unknown) => {
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'content' in output) return (output as Record<string, unknown>).content
  return JSON.stringify(output, null, 2)
}

// ── 参数引用：获取可引用的上游节点参数列表 ──
function getReferenceableParams(): { label: string; value: string }[] {
  const params: { label: string; value: string }[] = []
  if (!selectedNode.value) return params
  for (const n of nodes.value) {
    if (n.id === selectedNode.value.id) continue
    const nodeType = n.data?.type as string || ''
    const nodeLabel = (n.data?.label as string) || n.id
    const outputFields = getNodeOutputFields(nodeType)
    for (const field of outputFields) {
      params.push({
        label: `${nodeLabel}.${field}`,
        value: `${n.id}.${field}`,
      })
    }
  }
  return params
}

function getLlmNodeProvider(node: any): string {
  // 通用 llm 节点用 node.data.provider；特定 provider 节点从类型推导
  if (node?.type === 'llm') return node?.data?.provider || 'deepseek'
  return (node?.type || '').replace('llm_', '')
}

function getNodeOutputFields(nodeType: string): string[] {
  switch (nodeType) {
    case 'input': return ['user_input']
    case 'llm': case 'llm_openai': case 'llm_deepseek': case 'llm_qwen':
      return ['output', 'tokens']
    case 'tool_search': return ['results', 'count']
    case 'tool_tts': return ['audioUrl', 'fileName', 'output']
    case 'memory': return ['result', 'output']
    case 'code': return ['output']
    case 'api_call': return ['output', 'status']
    case 'transform': return ['output']
    default: return ['output']
  }
}

// ── 模板变量校验 ──
function validateTemplateParams(template: string, definedParams: string[]): string[] {
  const matches = template.matchAll(/\{\{(\w+)\}\}/g)
  const missing: string[] = []
  const defined = new Set(definedParams)
  for (const m of matches) {
    if (!defined.has(m[1])) {
      missing.push(m[1])
    }
  }
  return missing
}

// 拖拽处理 — 支持动态节点定义
const onDragStart = (event: DragEvent, node: NodeDefinition | Record<string, unknown>) => {
  if (event.dataTransfer) {
    // 适配 NodeDefinition 的字段名 (node_type->type, name->label)
    const dragData = {
      type: (node as any).node_type || (node as any).type,
      label: (node as any).name || (node as any).label,
      description: (node as any).description || '',
    }
    event.dataTransfer.setData('application/vueflow', JSON.stringify(dragData))
    event.dataTransfer.effectAllowed = 'move'
  }
}

const onDrop = (event: DragEvent) => {
  const data = event.dataTransfer?.getData('application/vueflow')
  if (!data) return

  const nodeType = JSON.parse(data)
  const rect = (event.target as HTMLElement).getBoundingClientRect()
  const position = {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  }

  const defaultData: Record<string, unknown> = {
    systemPrompt: '', temperature: 0.7, maxTokens: 2048,
    prompt: '', topK: 5, scoreThreshold: 0.5, condition: '',
    memoryType: 'short_term', action: 'store',
    method: 'GET', url: '', timeout: 30,
    language: 'python', code: '', transformType: 'json_extract',
    inputParams: [], outputParams: [],  // 参数引用系统
    skillName: '', skillId: '',           // Skill 选择器
    useGlobalConfig: false,               // LLM 全局配置引用
    configProvider: '',                   // 全局配置的 provider
  }

  const newNode: WorkflowNode = {
    id: `${nodeType.type}_${Date.now()}`,
    type: nodeType.type,
    position,
    data: { label: nodeType.label, type: nodeType.type, ...defaultData }
  }

  workflowStore.addNode(newNode)
}

const onNodeClick = (event: { node: WorkflowNode }) => {
  workflowStore.selectNode(event.node)
}

const onConnect = (params: Connection) => {
  const newEdge: WorkflowEdge = {
    id: `edge_${Date.now()}`,
    source: params.source,
    target: params.target,
    sourceHandle: params.sourceHandle ?? undefined,
    targetHandle: params.targetHandle ?? undefined,
    animated: true,
    markerEnd: MarkerType.ArrowClosed as unknown as string
  }
  workflowStore.addEdge(newEdge)
}

// ── 参数管理辅助函数 ──
function addLlmInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeLlmInputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'string', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeInputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeOutputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// LLM 输出参数管理
function addLlmOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', type: 'string', description: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeLlmOutputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// TTS 参数辅助
function addTtsInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeTtsInputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addTtsOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeTtsOutputParam(idx: any) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(Number(idx), 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// TTS 音色选项
const ttsVoiceOptions = [
  { label: 'Cherry (芊悦)', value: 'Cherry' },
  { label: 'Serena (苏瑶)', value: 'Serena' },
  { label: 'Ethan (晨煦)', value: 'Ethan' },
  { label: 'Chelsie (千雪)', value: 'Chelsie' },
  { label: 'Momo (茉兔)', value: 'Momo' },
  { label: 'Vivian (十三)', value: 'Vivian' },
  { label: 'Moon (月白)', value: 'Moon' },
  { label: 'Maia (四月)', value: 'Maia' },
  { label: 'Kai (凯)', value: 'Kai' },
  { label: 'Nofish (不吃鱼)', value: 'Nofish' },
  { label: 'Bella (萌宝)', value: 'Bella' },
  { label: 'Jennifer (詹妮弗)', value: 'Jennifer' },
  { label: 'Ryan (甜茶)', value: 'Ryan' },
  { label: 'Katerina (卡捷琳娜)', value: 'Katerina' },
  { label: 'Aiden (艾登)', value: 'Aiden' },
]

const deleteSelectedNode = () => {
  if (selectedNode.value) {
    workflowStore.removeNode(selectedNode.value.id)
    workflowStore.selectNode(null)
  }
}

const clearCanvas = () => {
  workflowStore.clearWorkflow()
}

const fitView = () => {
  doFitView()
}

// 保存工作流
const saveWorkflow = async () => {
  if (!workflowStore.workflowName.trim()) {
    message.error('请输入工作流名称')
    return
  }

  saving.value = true
  try {
    const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig

    if (workflowStore.currentWorkflowId) {
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
    } else {
      const res = await createWorkflow({
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
      workflowStore.currentWorkflowId = res.data?.data?.id
      // Update URL without navigation
      if (workflowStore.currentWorkflowId) {
        router.replace({ query: { id: String(workflowStore.currentWorkflowId) } })
      }
    }
    return true
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || '保存失败')
    return false
  } finally {
    saving.value = false
  }
}

// 自动保存 — 500ms 防抖
const autoSave = () => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(async () => {
    if (!workflowStore.currentWorkflowId || !workflowStore.workflowName.trim()) return
    try {
      const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
      console.log('[auto-save] 工作流已自动保存')
    } catch {
      // Auto-save failures are silent
    }
  }, 500)
}

// 监听画布变化 → 触发自动保存
watch(
  () => [workflowStore.nodes, workflowStore.edges, workflowStore.workflowName],
  () => {
    if (workflowStore.currentWorkflowId) {
      autoSave()
    }
  },
  { deep: true }
)

// 监听选中节点配置变化 → 自动保存到节点 data
watch(
  () => selectedNode.value?.data,
  () => {
    if (selectedNode.value) {
      workflowStore.updateNodeData(selectedNode.value.id, { ...selectedNode.value.data })
      // 同时触发自动保存到后端
      if (workflowStore.currentWorkflowId) {
        autoSave()
      }
    }
  },
  { deep: true }
)

// 调试执行
const openDebugPanel = () => {
  showDebugPanel.value = true
  workflowStore.resetExecution()
}

const executeWorkflow = async () => {
  if (!testInput.value.trim()) {
    message.error('请输入测试数据')
    return
  }

  if (!workflowStore.currentWorkflowId) {
    await saveWorkflow()
  }

  if (!workflowStore.currentWorkflowId) {
    message.error('请先保存工作流')
    return
  }

  executing.value = true
  workflowStore.startExecution()
  workflowStore.addExecutionLog('🚀 开始执行工作流...')

  try {
    const res = await execWorkflow(workflowStore.currentWorkflowId, { text: testInput.value })
    const result = res.data?.data

    if (result?.node_results) {
      for (const nodeResult of result.node_results) {
        executingNodeId.value = nodeResult.node_id
        const status = nodeResult.status || 'success'
        const icon = status === 'success' ? '✅' : status === 'failed' ? '❌' : '📊'
        workflowStore.updateNodeExecution({
          nodeId: nodeResult.node_id,
          nodeType: nodeResult.node_type,
          status,
          output: nodeResult.output,
          duration: nodeResult.duration
        })
        workflowStore.addExecutionLog(`${icon} [${getNodeLabel(nodeResult.node_type)}] ${status === 'success' ? '完成' : '失败'} (${nodeResult.duration || 0}ms)`)
      }
    }

    if (result?.output) {
      workflowStore.setExecutionComplete(result.output)
      workflowStore.addExecutionLog('✅ 工作流执行成功!')
    } else {
      workflowStore.setExecutionComplete(result)
      workflowStore.addExecutionLog('✅ 工作流执行完成')
    }

  } catch (error: unknown) {
    const errMsg = error instanceof Error ? error.message : '未知错误'
    workflowStore.addExecutionLog(`❌ 执行失败: ${errMsg}`)
    message.error('执行失败')
  } finally {
    executing.value = false
    executingNodeId.value = null
  }
}

// ── 工作流加载列表 ──
async function openLoadModal() {
  showLoadModal.value = true
  loadingWorkflows.value = true
  try {
    const res = await getWorkflows()
    workflowList.value = (res.data?.data?.items || res.data?.data || [])
  } catch {
    message.error('获取工作流列表失败')
  } finally {
    loadingWorkflows.value = false
  }
}

async function loadWorkflowById(id: number) {
  showLoadModal.value = false
  try {
    const res = await getWorkflow(id)
    const workflow = res.data?.data
    if (workflow) {
      workflowStore.loadWorkflow(workflow)
      if (workflow.engine_type) engineType.value = workflow.engine_type as 'dag' | 'langgraph'
      router.replace({ query: { id: String(id) } })
      message.success('工作流加载成功')
    }
  } catch {
    message.error('加载工作流失败')
  }
}

function handleCreateNew() {
  workflowStore.clearWorkflow()
  router.replace({ query: {} })
  message.info('已创建新工作流')
}

// ── 节点定义加载 ──
async function loadNodeDefinitions() {
  try {
    const res = await getNodeDefinitions()
    const defs = res.data?.data
    if (defs && Array.isArray(defs) && defs.length > 0) {
      nodeDefinitions.value = defs
      nodeDefsLoaded.value = true
    }
  } catch {
    // 使用硬编码回退
    nodeDefinitions.value = FALLBACK_NODES
    nodeDefsLoaded.value = true
  }
}

onMounted(async () => {
  // 并行加载所有初始化数据
  await Promise.all([
    loadNodeDefinitions(),
    llmConfigStore.fetchAll(),
    loadSkills(),
  ])

  const id = route.query.id as string
  if (id) {
    await loadWorkflowById(parseInt(id))
  }
})

onUnmounted(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
})

export function useWorkflowEditor() {


// Icon mapping for node types
const NODE_ICON_MAP: Record<string, unknown> = {
  llm_openai: HardwareChipOutline,
  llm_deepseek: HardwareChipOutline,
  llm_qwen: ChatbubbleEllipsesOutline,
  tool_search: SearchOutline,
  tool_tts: VolumeHighOutline,
  input: EnterOutline,
  output: ExitOutline,
  condition: GitBranchOutline,
  router: CompassOutline,
  memory: ServerOutline,
  code: CodeSlashOutline,
  api_call: GlobeOutline,
  transform: SyncOutline,
}

const getNodeIconComponent = (type: string) => NODE_ICON_MAP[type] || AppsOutline


// 节点组件


  return {
  NODE_ICON_MAP,
  getNodeIconComponent,
  route,
  router,
  message,
  workflowStore,
  llmConfigStore,
  doFitView,
  defaultEdgeOptions,
  nodeDefinitions,
  nodeDefsLoaded,
  nodeDefsByCategory,
  CATEGORY_LABELS,
  CATEGORY_ICONS,
  FALLBACK_NODES,
  llmNodes,
  toolNodes,
  ioNodes,
  logicNodes,
  dataNodes,
  hasDynamicDefs,
  openaiModels,
  deepseekModels,
  qwenModels,
  memoryTypeOptions,
  memoryActionOptions,
  httpMethods,
  codeLanguages,
  transformTypes,
  skills,
  skillsLoaded,
  loadSkills,
  saving,
  executing,
  executingNodeId,
  showDebugPanel,
  showLLMConfig,
  showLoadModal,
  workflowList,
  loadingWorkflows,
  testInput,
  vueFlowRef,
  engineType,
  engineTypeOptions,
  editingConfigId,
  llmConfigForm,
  resetLlmConfigForm,
  llmConfigColumns,
  editConfig,
  handleSaveConfig,
  handleDeleteConfigById,
  handleDeleteConfig,
  handleSetDefault,
  autoSaveTimer,
  nodes,
  edges,
  selectedNodeId,
  selectedNode,
  nodeData,
  executionResults,
  executionLogs,
  finalOutput,
  executionStatus,
  executionProgress,
  completedNodes,
  statusText,
  nodeColor,
  loadTemplate,
  getNodeLabel,
  formatOutput,
  getReferenceableParams,
  getLlmNodeProvider,
  getNodeOutputFields,
  validateTemplateParams,
  onDragStart,
  onDrop,
  onNodeClick,
  onConnect,
  addLlmInputParam,
  removeLlmInputParam,
  addInputParam,
  removeInputParam,
  addOutputParam,
  removeOutputParam,
  addLlmOutputParam,
  removeLlmOutputParam,
  addTtsInputParam,
  removeTtsInputParam,
  addTtsOutputParam,
  removeTtsOutputParam,
  ttsVoiceOptions,
  deleteSelectedNode,
  clearCanvas,
  fitView,
  saveWorkflow,
  autoSave,
  openDebugPanel,
  executeWorkflow,
  openLoadModal,
  loadWorkflowById,
  handleCreateNew,
  loadNodeDefinitions,
  };
}
