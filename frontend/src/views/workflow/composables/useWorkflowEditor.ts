// Workflow editor composable — extracted from views/workflow/editor.vue

import { ref, computed, onMounted, watch, onUnmounted, h, nextTick } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useVueFlow, MarkerType, type Connection } from '@vue-flow/core'
import {
  NInput, NInputNumber, NCard, NCollapse, NCollapseItem,
  NDivider, NDrawer, NDrawerContent, NTimeline, NTimelineItem, NTag, NSpin, NProgress,
  NModal, NForm, NGrid, NFormItem, NSelect, NSwitch, NList, NListItem, NEmpty, NPopconfirm, NPopover,
  useDialog,
} from 'naive-ui'
import {
  SaveOutline, PlayOutline, ExpandOutline, SettingsOutline,
  AppsOutline, LocateOutline, CheckmarkCircleOutline, CloseCircleOutline,
  HardwareChipOutline, ChatbubbleEllipsesOutline, SearchOutline, VolumeHighOutline,
  EnterOutline, ExitOutline, GitBranchOutline, CompassOutline, ServerOutline,
  CodeSlashOutline, GlobeOutline, SyncOutline, RocketOutline, FlashOutline,
  FolderOpenOutline, AddOutline, CloseOutline
} from '@vicons/ionicons5'
import { useWorkflowStore } from '@/stores/workflow'
import { getWorkflow, createWorkflow, updateWorkflow, executeWorkflowStream, getWorkflows, getNodeDefinitions, type Workflow, type WorkflowNode, type WorkflowEdge, type WorkflowConfig, type NodeDefinition } from '@/api/workflow'
import { agentApi } from '@/api/agent'
import { apiUnwrap } from '@/utils/apiUnwrap'
import type { SkillInfo } from '@/types/agent'
import { useDedupedMessage } from '@/utils/message'
// 画布根组件（VueFlow/Background/Controls/MiniMap）的 import 归 editor.vue
// ——模板作用域解析要求绑定在编辑器文件本身（W1 修复）；此处仅保留样式副作用
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

// ── setup 级依赖（模块顶层只声明，获取一律发生在 useWorkflowEditor() 内）──
// A_sweep 实锤（round2 #1）：旧实现在模块顶层调用 useRoute/useRouter/
// useDedupedMessage/useVueFlow。模块首次求值发生在组件 setup 之外，inject()
// 拿不到上下文（Vue 警告 inject() can only be used inside setup()）→ message
// 为 undefined → 任意 toast（如 loadTemplate 的「模板加载成功」）抛 TypeError，
// 点「快速模板」即整页进 ErrorBoundary。onMounted/onUnmounted/watch 写在这里
// 同样不会随组件挂载执行（loadNodeDefinitions 等初始化永不发生）。
// 现在这些调用全部延迟到 useWorkflowEditor()（由 <script setup> 同步调用）内，
// 保证组件生命周期之外零执行。
let route!: ReturnType<typeof useRoute>
let router!: ReturnType<typeof useRouter>
let message!: ReturnType<typeof useDedupedMessage>
let workflowStore!: ReturnType<typeof useWorkflowStore>
let doFitView!: ReturnType<typeof useVueFlow>['fitView']
let t!: ReturnType<typeof useI18n>['t']
let dialog!: ReturnType<typeof useDialog>

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
    // GET /agent/skills → {success, data: <SkillInfo[]>} → apiUnwrap 解一层
    skills.value = apiUnwrap<SkillInfo[]>(res) || []
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
// ── 保存状态（B8）：工具栏状态点常驻，永远如实反映最近一次保存结果 ──
// unsaved=有未落库的改动 / saving=保存请求在途 / saved=服务端已确认 / error=最近一次保存失败
type SaveStatus = 'unsaved' | 'saving' | 'saved' | 'error'
const saveStatus = ref<SaveStatus>('unsaved')
const executing = ref(false)
const executingNodeId = ref<string | null>(null)
const showDebugPanel = ref(false)
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

// ── 自动保存定时器 ──
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

// ── 保存反馈（B8）──
// 自动保存失败提示节流：同一故障在窗口内只弹一次，避免每次防抖都打扰用户
const AUTO_SAVE_ERROR_TOAST_THROTTLE_MS = 10_000
let lastAutoSaveErrorToastAt = 0

// 画布编辑版本号：保存请求在途时用户又改动画布 → 请求完成后不得把状态误标为「已保存」
let canvasEditVersion = 0

/** 画布发生本地改动：版本号 +1，并把「已保存/保存中」如实降级为「未保存」 */
function markCanvasDirty() {
  canvasEditVersion++
  if (saveStatus.value === 'saved' || saveStatus.value === 'saving') {
    saveStatus.value = 'unsaved'
  }
}

// ── 离开保护（E12）：以「最近一次持久化快照」为 dirty 真值 ──
// 不能用 saveStatus !== 'saved'：刚进入编辑器（从未保存、也无改动）同样是
// 'unsaved'，会把「无改动离开」误判为 dirty。这里把落库载荷（getFlowData()
// 即保存请求体 + 工作流名）序列化后与基线比较：加载 / 手动保存 / 自动保存
// 成功都会刷新基线，只有相对基线发生实际内容变化才算 dirty。
const persistedBaseline = ref('')

function persistedSnapshot(): string {
  return JSON.stringify({
    name: workflowStore.workflowName,
    ...workflowStore.getFlowData(),
  })
}

const isDirty = computed(() => persistedSnapshot() !== persistedBaseline.value)

// ── 响应式布局（E12）：<1280 收窄时属性面板默认收起，可用开关以覆盖层展开 ──
// matchMedia 不可用（如旧测试环境未补桩）时按宽屏处理，面板保持可见。
const isNarrow = ref(false)
const propertyPanelOpen = ref(true)
let viewportQuery: MediaQueryList | null = null

function handleViewportChange(event: MediaQueryListEvent) {
  isNarrow.value = !event.matches
  // 断点切换跟随默认策略：宽屏展开为第三栏，窄屏收起（覆盖层可随时重开）
  propertyPanelOpen.value = event.matches
}

const togglePropertyPanel = () => {
  propertyPanelOpen.value = !propertyPanelOpen.value
}

const closePropertyPanel = () => {
  propertyPanelOpen.value = false
}

// 关闭/刷新页面保护：dirty 时触发浏览器原生「离开此网站？」确认
function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!isDirty.value) return
  // 现代浏览器以 preventDefault 为准；旧版 Chromium 需要 returnValue 非空
  event.preventDefault()
  event.returnValue = ''
}

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
  // 引擎成功态为 "completed"（ExecutionStatus，workflow_engine.py:37-42）——
  // 旧代码按 'success' 判定导致成功节点恒判失败（B10 词表漂移）
  if (executionResults.value.every(r => r.status === 'completed')) return 'success'
  return 'running'
})

const executionProgress = computed(() => {
  if (executionResults.value.length === 0) return 0
  const completed = executionResults.value.filter(r => r.status === 'completed').length
  return Math.round((completed / executionResults.value.length) * 100)
})

const completedNodes = computed(() => executionResults.value.filter(r => r.status === 'completed').length)

const statusText = computed(() => {
  const map: Record<string, string> = {
    idle: '等待执行',
    running: '执行中',
    success: '执行成功',
    failed: '执行失败'
  }
  return map[executionStatus.value] || '未知'
})

// ── 保存状态点文案（B8，i18n 四语；computed 在渲染期求值，此时 t 已在 setup 内就绪）──
const saveStatusText = computed(() => {
  switch (saveStatus.value) {
    case 'saved': return t('workflow.editor.saveStatusSaved')
    case 'saving': return t('workflow.editor.saveStatusSaving')
    case 'error': return t('workflow.editor.saveStatusError')
    default: return t('workflow.editor.saveStatusUnsaved')
  }
})

const saveRetryText = computed(() => t('workflow.editor.saveRetry'))

const saveStatusHint = computed(() =>
  saveStatus.value === 'error'
    ? t('workflow.editor.saveRetryHint')
    : saveStatusText.value
)

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

// 通用 LLM 节点的供应商选项（B3：全局配置入口移除后不再依赖 /llm-config 端点；
// 与后端 llm_config.SUPPORTED_PROVIDERS/PROVIDER_LABELS 同口径，排除通用 'llm'）
const llmProviderOptions = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'DeepSeek', value: 'deepseek' },
  { label: '通义千问', value: 'qwen' },
  { label: '智谱', value: 'zhipu' },
  { label: '阶跃星辰', value: 'step' },
  { label: 'AI平', value: 'ai_ping' },
]

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

// 节点构造（拖拽与点击添加共用）
const buildNode = (
  type: string,
  label: string | undefined,
  position: { x: number; y: number },
): WorkflowNode => {
  const defaultData: Record<string, unknown> = {
    systemPrompt: '', temperature: 0.7, maxTokens: 2048,
    prompt: '', topK: 5, scoreThreshold: 0.5, condition: '',
    memoryType: 'short_term', action: 'store',
    method: 'GET', url: '', timeout: 30,
    language: 'python', code: '', transformType: 'json_extract',
    inputParams: [], outputParams: [],  // 参数引用系统
    skillName: '', skillId: '',           // Skill 选择器
  }
  return {
    id: `${type}_${Date.now()}`,
    type,
    position,
    data: { label: label || type, type, ...defaultData }
  }
}

const onDrop = (event: DragEvent) => {
  const data = event.dataTransfer?.getData('application/vueflow')
  if (!data) return

  const nodeType = JSON.parse(data) as { type?: string; label?: string }
  if (!nodeType?.type) return
  const rect = (event.target as HTMLElement).getBoundingClientRect()
  const position = {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  }

  workflowStore.addNode(buildNode(nodeType.type, nodeType.label, position))
}

// 点击添加（触屏友好）：tap 只产生 click、没有 HTML5 drag 事件，
// 点节点卡片即以阶梯错位位置添加节点；拖拽路径原样保留。
// 添加后选中新节点：触屏无 hover，选中即展开属性面板形成明确反馈。
const CLICK_ADD_CASCADE_STEP = 36
const addNodeFromLibrary = (node: NodeDefinition | Record<string, unknown>) => {
  const type = ((node as any).node_type || (node as any).type) as string | undefined
  if (!type) return
  const label = ((node as any).name || (node as any).label) as string | undefined
  const count = workflowStore.nodes.length
  const offset = (count % 8) * CLICK_ADD_CASCADE_STEP
  const newNode = buildNode(type, label, { x: 120 + offset, y: 120 + offset })
  workflowStore.addNode(newNode)
  workflowStore.selectNode(newNode)
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

// 自动保存防抖窗口（毫秒）——清空确认文案里的「N 秒」与此同源，避免行为变化后文案失真
const AUTO_SAVE_DELAY_MS = 500

// 已持久化版本（source）的节点数：空画布保护判定「source 非空」的依据。
// 在加载 / 手动保存 / 自动保存成功时刷新；useWorkflowEditor() setup 时归零。
let lastPersistedNodeCount = 0

const clearCanvas = () => {
  // 误点即清空，且画布变更会触发 500ms 防抖自动保存 —— 先二次确认；
  // 确认后只清节点/连线，保留 currentWorkflowId 与名称：清空的是「当前工作流
  // 的画布」（丢弃工作流关联走「新建」入口）。空画布不会被自动保存静默写回，
  // 只有用户手动点「保存」才会持久化空版本。
  dialog.warning({
    title: t('workflow.editor.clearConfirmTitle'),
    content: t('workflow.editor.clearConfirmContent', { n: AUTO_SAVE_DELAY_MS / 1000 }),
    positiveText: t('workflow.editor.clearConfirmPositive'),
    negativeText: t('workflow.editor.clearConfirmNegative'),
    onPositiveClick: () => {
      workflowStore.setNodes([])
      workflowStore.setEdges([])
      workflowStore.selectNode(null)
    },
  })
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
  saveStatus.value = 'saving'
  const versionAtRequest = canvasEditVersion
  try {
    const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig
    // 落库载荷快照：成功后作为离开保护的「已保存」基线（请求在途期间的新改动
    // 仍会与快照不同 → dirty 保持为真，不会因一次在途保存误判干净）
    const requestSnapshot = JSON.stringify({ name: workflowStore.workflowName, ...flowData })

    if (workflowStore.currentWorkflowId) {
      // B11 契约对齐：后端 WorkflowUpdate/Workflow 模型均无 engine_type 字段
      // （models/workflow.py、schemas/workflow.py:45-51），旧实现随载荷发送属
      // 「静默不落库」的假持久化——不再发送
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
      })
    } else {
      const res = await createWorkflow({
        name: workflowStore.workflowName,
        flow_data: flowData,
      })
      workflowStore.currentWorkflowId = apiUnwrap<{ id: number }>(res)?.id
      // Update URL without navigation
      if (workflowStore.currentWorkflowId) {
        router.replace({ query: { id: String(workflowStore.currentWorkflowId) } })
      }
    }
    // 手动保存是显式动作（允许保存空画布），同步刷新已持久化节点数
    lastPersistedNodeCount = flowData.nodes.length
    // 离开保护：服务端已确认的这份载荷成为新的 dirty 基线
    persistedBaseline.value = requestSnapshot
    // B8：服务端确认后才置「已保存」；请求在途期间画布又被改动则保持「未保存」
    if (canvasEditVersion === versionAtRequest) {
      saveStatus.value = 'saved'
    }
    // 手动保存是显式动作 → 成功给一次 toast；自动保存成功不弹（状态点常驻即可）
    message.success(t('workflow.editor.saveSuccess'))
    return true
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    // B8：失败不得沉默，状态点不得停在/进入「已保存」
    saveStatus.value = 'error'
    message.error(err.response?.data?.detail || t('workflow.editor.saveFailed'))
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
    // 空画布保护：节点=0 且已保存版本非空（source 非空）时不得静默写回——
    // 误删最后一个节点 / 确认清空画布后，防抖到点会把服务端版本覆盖成空。
    // 改为明确提示并跳过本次写回；确需保存空画布走手动「保存」（显式动作）。
    if (workflowStore.nodes.length === 0 && lastPersistedNodeCount > 0) {
      message.warning(t('workflow.editor.emptyCanvasAutoSaveBlocked'))
      return
    }
    const versionAtRequest = canvasEditVersion
    saveStatus.value = 'saving'
    try {
      const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig
      const requestSnapshot = JSON.stringify({ name: workflowStore.workflowName, ...flowData })
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
      })
      lastPersistedNodeCount = flowData.nodes.length
      // 离开保护：自动保存成功同样刷新 dirty 基线（已落库即非 dirty）
      persistedBaseline.value = requestSnapshot
      // B8：只有服务端确认且请求期间画布无新改动，才允许置「已保存」
      if (canvasEditVersion === versionAtRequest) {
        saveStatus.value = 'saved'
      }
      console.log('[auto-save] 工作流已自动保存')
    } catch (error: unknown) {
      // B8：自动保存失败不再沉默——状态点置「保存失败」（绝不显示已保存），
      // 错误提示按节流窗口弹出（同一故障不反复打扰），点击状态点可重试。
      saveStatus.value = 'error'
      const now = Date.now()
      if (now - lastAutoSaveErrorToastAt >= AUTO_SAVE_ERROR_TOAST_THROTTLE_MS) {
        lastAutoSaveErrorToastAt = now
        const err = error as { response?: { data?: { detail?: string } } }
        const detail = err.response?.data?.detail
        message.error(
          detail
            ? `${t('workflow.editor.autoSaveFailed')}: ${detail}`
            : t('workflow.editor.autoSaveFailed'),
        )
      }
    }
  }, AUTO_SAVE_DELAY_MS)
}

// 自动保存失败后的显式重试入口（点击状态点）：复用手动保存路径（含成功 toast）
const retrySave = async () => {
  if (saveStatus.value !== 'error') return
  await saveWorkflow()
}

// 监听画布变化 → 触发自动保存：注册在 useWorkflowEditor() 内（见文件头部说明，
// 模块顶层注册不随组件生命周期挂载/释放）

// 调试执行
const openDebugPanel = () => {
  showDebugPanel.value = true
  workflowStore.resetExecution()
}

// 在途流式执行的停止函数（组件卸载/重开执行时中止底层 SSE 请求）
let stopActiveExecution: (() => void) | null = null

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

  // 流式执行（接线 issue #289 的 executeWorkflowStream；旧入口用非流式
  // executeWorkflow——整段等待且是 B8/B9 双重漂移的源头）。
  // 事件序列（workflow.py:239-368）：workflow_start → 引擎实时 node_start /
  // node_complete / node_error → 失败路径 workflow_error→error+done；
  // 成功路径端点回放一轮 node_complete（落库结果，含 status 全字段）后发
  // complete。node_complete 成功路径会出现两帧（实时 + 回放），日志按节点去重。
  stopActiveExecution?.()
  executing.value = true
  workflowStore.startExecution()
  workflowStore.addExecutionLog('🚀 开始执行工作流...')

  const loggedNodes = new Set<string>()
  await new Promise<void>((resolve) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      stopActiveExecution = null
      executing.value = false
      executingNodeId.value = null
      resolve()
    }

    const logNodeOnce = (nodeId: string, line: string) => {
      if (loggedNodes.has(nodeId)) return
      loggedNodes.add(nodeId)
      workflowStore.addExecutionLog(line)
    }

    stopActiveExecution = executeWorkflowStream(
      workflowStore.currentWorkflowId!,
      { text: testInput.value },
      (event, data) => {
        switch (event) {
          case 'node_start': {
            // 实时帧 {node_id, node_type, timestamp}（workflow_engine.py:816-820）
            executingNodeId.value = (data as { node_id?: string }).node_id ?? null
            break
          }
          case 'node_complete': {
            // 实时帧 {node_id,node_type,output,duration}（workflow_engine.py:851-857）
            // 与端点回放帧（NodeResult 全字段，workflow.py:317-319）同键不同完整度
            const r = data as { node_id: string; node_type?: string; status?: string; output?: unknown; duration?: number | null }
            const status = (r.status === 'failed' ? 'failed' : 'completed') as 'completed' | 'failed'
            workflowStore.updateNodeExecution({
              nodeId: r.node_id,
              nodeType: r.node_type ?? '',
              status,
              output: r.output as Record<string, unknown> | undefined,
              duration: r.duration ?? undefined
            })
            const icon = status === 'failed' ? '❌' : '✅'
            logNodeOnce(r.node_id, `${icon} [${getNodeLabel(r.node_type ?? '')}] ${status === 'failed' ? '失败' : '完成'} (${r.duration ?? 0}ms)`)
            break
          }
          case 'node_error': {
            // 引擎实时失败帧 {node_id,error,traceback}（workflow_engine.py:870-875）
            const r = data as { node_id: string; error?: string }
            workflowStore.updateNodeExecution({
              nodeId: r.node_id,
              nodeType: '',
              status: 'failed',
              error: r.error
            })
            logNodeOnce(r.node_id, `❌ [${r.node_id}] 失败: ${r.error || '未知错误'}`)
            break
          }
          case 'complete': {
            // 引擎总体结果 {success,output,node_outputs,context,memory}
            // （workflow_engine.py:1008-1016）；success=false 时带 error 摘要
            const engineResult = (data ?? {}) as { success?: boolean; output?: unknown; error?: string }
            if (engineResult.success === false) {
              workflowStore.setExecutionComplete(null)
              workflowStore.addExecutionLog(`❌ 执行失败: ${engineResult.error || '工作流节点执行失败'}`)
              message.error('执行失败')
            } else {
              const finalOut = engineResult.output !== undefined ? engineResult.output : null
              workflowStore.setExecutionComplete((finalOut ?? null) as Record<string, unknown> | string | null)
              workflowStore.addExecutionLog('✅ 工作流执行成功!')
            }
            finish()
            break
          }
          case 'error': {
            // 后端错误收口帧（#281：error 后必补 done）——done / onClose 再收尾
            const d = (data ?? {}) as { error?: string }
            workflowStore.addExecutionLog(`❌ 执行失败: ${d.error || '未知错误'}`)
            message.error('执行失败')
            break
          }
          default:
            // heartbeat / workflow_start / workflow_complete 等：无 UI 语义，忽略
            break
        }
      },
      // 流关闭统一收尾（正常结束 / abort / 网络异常），防 executing 悬挂
      finish,
    )
  })
}

// ── 工作流加载列表 ──
async function openLoadModal() {
  showLoadModal.value = true
  loadingWorkflows.value = true
  try {
    const res = await getWorkflows()
    // GET /workflows/ → {success, data: {items: [...], total}} → apiUnwrap 归一
    const page = apiUnwrap<{ items?: { id: number; name: string; created_at: string }[] } | null>(res)
    workflowList.value = page?.items || []
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
    const workflow = apiUnwrap<Workflow>(res)
    if (workflow) {
      workflowStore.loadWorkflow(workflow)
      // 记录已持久化版本的节点数：空画布保护的「source 非空」判定依据
      lastPersistedNodeCount = workflow.flow_data?.nodes?.length ?? 0
      // B8：加载到的就是服务端已保存版本。等深度 watcher 先消化掉加载引发的
      // 「变更」标记（否则状态点会先被置脏），再把状态点如实置为「已保存」。
      await nextTick()
      saveStatus.value = 'saved'
      // 离开保护：加载进来的版本即 dirty 基线（刚加载不应触发离开确认）
      persistedBaseline.value = persistedSnapshot()
      // B11：详响应无 engine_type（workflow.py:121-132），旧读点恒 undefined——
      // 移除；选择器保留为本地展示态（见模板 disabled 提示）
      router.replace({ query: { id: String(id) } })
      message.success('工作流加载成功')
    }
  } catch {
    message.error('加载工作流失败')
  }
}

function handleCreateNew() {
  workflowStore.clearWorkflow()
  lastPersistedNodeCount = 0
  // B8：新工作流尚无任何版本落库 → 状态点如实显示「未保存」
  saveStatus.value = 'unsaved'
  // 离开保护：新建后画布为空、无改动 → 基线即为空画布（静默放行）
  persistedBaseline.value = persistedSnapshot()
  router.replace({ query: {} })
  message.info('已创建新工作流')
}

// ── 节点定义加载 ──
async function loadNodeDefinitions() {
  try {
    const res = await getNodeDefinitions()
    // GET /workflows/nodes/definitions → {success, data: <NodeDefinition[]>}
    const defs = apiUnwrap<NodeDefinition[]>(res)
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

// 初始化加载与卸载清理：注册在 useWorkflowEditor() 内（模块顶层注册不会执行）

export function useWorkflowEditor() {
  // ── setup 级依赖获取（必须在组件 setup 内执行；背景见文件头部说明）──
  route = useRoute()
  router = useRouter()
  message = useDedupedMessage()
  workflowStore = useWorkflowStore()
  doFitView = useVueFlow().fitView
  t = useI18n().t
  dialog = useDialog()
  // 新编辑器实例：已持久化节点数从未知开始（加载/保存路径会刷新）
  lastPersistedNodeCount = 0
  // B8：保存状态按新实例重置（模块级单例在测试/复用场景下必须归零）
  saveStatus.value = 'unsaved'
  canvasEditVersion = 0
  lastAutoSaveErrorToastAt = 0

  // E12 离开保护：进入编辑器时的画布内容即 dirty 基线（未改动 → 静默放行）
  persistedBaseline.value = persistedSnapshot()

  // E12 响应式断点：≥1280 三栏；<1280 属性面板默认收起（覆盖层可重开）
  viewportQuery = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 1280px)')
    : null
  isNarrow.value = viewportQuery ? !viewportQuery.matches : false
  propertyPanelOpen.value = !isNarrow.value

  // ── 画布变更 → 自动保存（组件级注册，随组件卸载自动释放）──
  watch(
    () => [workflowStore.nodes, workflowStore.edges, workflowStore.workflowName],
    () => {
      // B8：本地改动发生 → 状态点如实回退为「未保存」
      markCanvasDirty()
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
        const selected = selectedNode.value
        const storeNode = workflowStore.nodes.find((n) => n.id === selected.id)
        // 递归防护（E12）：点击添加会程序化选中新节点，而选中对象通常就是
        // store 里的同一引用（属性面板 v-model 直接改的就是它）。此时若再
        // updateNodeData({ ...selected.data })，每次都会生成新的 data 对象
        // → 深度 watcher 自触发直至「Maximum recursive updates」上限。
        // 引用相同说明数据已同步到 store，跳过回写。
        if (!storeNode || storeNode.data !== selected.data) {
          workflowStore.updateNodeData(selected.id, { ...selected.data })
        }
        markCanvasDirty()
        // 同时触发自动保存到后端
        if (workflowStore.currentWorkflowId) {
          autoSave()
        }
      }
    },
    { deep: true }
  )

  // ── 离开保护（E12）：dirty 时路由离开二次确认；已保存/无改动静默放行 ──
  // 确认框复用 useDialog（与清空画布同一交互形态）。positive 才放行；
  // negative / 遮罩关闭都留在编辑器。
  onBeforeRouteLeave(() => {
    if (!isDirty.value) return true
    return new Promise<boolean>((resolve) => {
      dialog.warning({
        title: t('workflow.editor.leaveConfirmTitle'),
        content: t('workflow.editor.leaveConfirmContent'),
        positiveText: t('workflow.editor.leaveConfirmPositive'),
        negativeText: t('workflow.editor.leaveConfirmNegative'),
        onPositiveClick: () => { resolve(true) },
        onNegativeClick: () => { resolve(false) },
        onClose: () => { resolve(false) },
      })
    })
  })

  // ── 生命周期：初始化加载 + 卸载清理 ──
  onMounted(async () => {
    // E12：关闭/刷新页面保护 + 断点监听（随组件卸载移除）
    window.addEventListener('beforeunload', handleBeforeUnload)
    if (viewportQuery) {
      if (typeof viewportQuery.addEventListener === 'function') {
        viewportQuery.addEventListener('change', handleViewportChange)
      } else if (typeof viewportQuery.addListener === 'function') {
        viewportQuery.addListener(handleViewportChange)
      }
    }

    // 并行加载所有初始化数据
    await Promise.all([
      loadNodeDefinitions(),
      loadSkills(),
    ])

    const id = route.query.id as string
    if (id) {
      await loadWorkflowById(parseInt(id))
    }
  })

  onUnmounted(() => {
    window.removeEventListener('beforeunload', handleBeforeUnload)
    if (viewportQuery) {
      if (typeof viewportQuery.removeEventListener === 'function') {
        viewportQuery.removeEventListener('change', handleViewportChange)
      } else if (typeof viewportQuery.removeListener === 'function') {
        viewportQuery.removeListener(handleViewportChange)
      }
      viewportQuery = null
    }
    if (autoSaveTimer) clearTimeout(autoSaveTimer)
    // 组件卸载时中止在途流式执行（onClose 回调负责收尾 executing 状态）
    stopActiveExecution?.()
  })

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

  return {
  NODE_ICON_MAP,
  getNodeIconComponent,
  route,
  router,
  message,
  t,
  workflowStore,
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
  saveStatus,
  saveStatusText,
  saveRetryText,
  saveStatusHint,
  isDirty,
  isNarrow,
  propertyPanelOpen,
  togglePropertyPanel,
  closePropertyPanel,
  executing,
  executingNodeId,
  showDebugPanel,
  showLoadModal,
  workflowList,
  loadingWorkflows,
  testInput,
  vueFlowRef,
  engineType,
  engineTypeOptions,
  llmProviderOptions,
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
  getNodeOutputFields,
  validateTemplateParams,
  onDragStart,
  onDrop,
  addNodeFromLibrary,
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
  retrySave,
  autoSave,
  openDebugPanel,
  executeWorkflow,
  openLoadModal,
  loadWorkflowById,
  handleCreateNew,
  loadNodeDefinitions,
  };
}
