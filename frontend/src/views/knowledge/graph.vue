<template>
  <div class="graph-page h-full min-h-0 flex flex-col bg-gray-50 dark:bg-gray-950">
    <!-- T9 定位三件套：面包屑（DocMind / 知识库 / 知识图谱）+ 返回出口（→ 知识库） -->
    <div class="border-b border-[var(--c-border)] bg-[var(--c-surface)] px-5 py-2">
      <PageBreadcrumb :show-back="true" />
    </div>

    <!-- Header -->
    <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-blue-500/10 flex items-center justify-center">
          <n-icon size="20" class="text-blue-500"><GitNetworkOutline /></n-icon>
        </div>
        <div>
          <h1 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('router.knowledgeGraph') }}</h1>
          <p class="text-xs text-gray-500 dark:text-gray-400">{{ t('knowledge.graph.subtitle') }}</p>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <n-input
          v-model:value="searchQuery"
          placeholder="搜索实体..."
          clearable
          round
          size="small"
          class="w-48"
          @keyup.enter="fetchGraph"
        >
          <template #prefix><n-icon><SearchOutline /></n-icon></template>
        </n-input>
        <n-button size="small" @click="fetchGraph" :loading="loading">
          <template #icon><n-icon><RefreshOutline /></n-icon></template>
          刷新
        </n-button>
      </div>
    </div>

    <!-- Content -->
    <div class="flex-1 flex min-h-0">
      <!-- Graph Canvas -->
      <div class="flex-1 relative" ref="canvasContainer">
        <canvas
          ref="canvas"
          class="w-full h-full cursor-grab active:cursor-grabbing"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
          @mouseup="onMouseUp"
          @wheel.prevent="onWheel"
        />
        <!-- 缩放控件：放大 / 缩小 / 复位（fitView 自动适配） -->
        <div class="absolute top-4 left-4 flex flex-col gap-1 bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-xl border border-gray-200 dark:border-gray-700 p-1">
          <n-button
            size="tiny"
            quaternary
            circle
            :title="t('knowledge.graph.zoomIn')"
            :aria-label="t('knowledge.graph.zoomIn')"
            data-testid="graph-zoom-in"
            @click="zoomIn"
          >
            <template #icon><n-icon><AddOutline /></n-icon></template>
          </n-button>
          <n-button
            size="tiny"
            quaternary
            circle
            :title="t('knowledge.graph.zoomOut')"
            :aria-label="t('knowledge.graph.zoomOut')"
            data-testid="graph-zoom-out"
            @click="zoomOut"
          >
            <template #icon><n-icon><RemoveOutline /></n-icon></template>
          </n-button>
          <n-button
            size="tiny"
            quaternary
            circle
            :title="t('knowledge.graph.fitView')"
            :aria-label="t('knowledge.graph.fitView')"
            data-testid="graph-fit-view"
            @click="fitView"
          >
            <template #icon><n-icon><ExpandOutline /></n-icon></template>
          </n-button>
        </div>
        <!-- Legend -->
        <div class="absolute bottom-4 left-4 bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-xl border border-gray-200 dark:border-gray-700 p-3 text-xs">
          <div class="font-medium text-gray-700 dark:text-gray-300 mb-2">实体类型</div>
          <div class="flex flex-wrap gap-2">
            <div v-for="(color, type) in typeColors" :key="type" class="flex items-center gap-1">
              <span class="w-3 h-3 rounded-full" :style="{ backgroundColor: color }"></span>
              <span class="text-gray-600 dark:text-gray-400">{{ typeLabels[type] || type }}</span>
            </div>
          </div>
        </div>
        <!-- Stats -->
        <div class="absolute top-4 right-4 bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-xl border border-gray-200 dark:border-gray-700 p-3 text-xs">
          <div class="flex gap-4">
            <div><span class="text-gray-500">节点:</span> <span class="font-bold text-blue-600">{{ graphNodes.length }}</span></div>
            <div><span class="text-gray-500">关系:</span> <span class="font-bold text-blue-600">{{ graphEdges.length }}</span></div>
          </div>
        </div>
        <!-- 诚实空态 / 错误态：不再以"白画布"代替数据状态 -->
        <div
          v-if="!loading && !loadFailed && graphNodes.length === 0"
          class="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none bg-white/60 dark:bg-gray-950/60"
        >
          <n-icon size="56" class="text-gray-300 dark:text-gray-600 mb-4"><GitNetworkOutline /></n-icon>
          <p class="font-medium text-gray-500 dark:text-gray-400">{{ t('knowledge.graph.emptyTitle') }}</p>
          <p class="text-sm text-gray-400 dark:text-gray-500 mt-1">{{ t('knowledge.graph.emptyDesc') }}</p>
        </div>
        <div
          v-else-if="loadFailed"
          class="absolute inset-0 flex flex-col items-center justify-center text-center bg-white/70 dark:bg-gray-950/70"
        >
          <n-icon size="56" class="text-gray-300 dark:text-gray-600 mb-4"><WarningOutline /></n-icon>
          <p class="font-medium text-gray-500 dark:text-gray-400">{{ t('knowledge.graph.loadFailed') }}</p>
          <n-button size="small" class="mt-4" @click="fetchGraph">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            {{ t('common.refresh') }}
          </n-button>
        </div>
      </div>

      <!-- Side Panel -->
      <div v-if="selectedNode" class="w-72 border-l border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 overflow-y-auto">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-gray-900 dark:text-white text-sm">实体详情</h3>
          <n-button size="tiny" quaternary circle @click="selectedNode = null">
            <template #icon><n-icon><CloseOutline /></n-icon></template>
          </n-button>
        </div>
        <div class="space-y-3">
          <div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">名称</div>
            <div class="text-sm font-medium text-gray-900 dark:text-white">{{ selectedNode.id }}</div>
          </div>
          <div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">类型</div>
            <n-tag size="small" :bordered="false" :style="{ backgroundColor: typeColors[selectedNode.type] + '20', color: typeColors[selectedNode.type] }">
              {{ typeLabels[selectedNode.type] || selectedNode.type }}
            </n-tag>
          </div>
          <div v-if="selectedNode.description">
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">描述</div>
            <div class="text-sm text-gray-700 dark:text-gray-300">{{ selectedNode.description }}</div>
          </div>
          <div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">出现次数</div>
            <div class="text-sm font-medium text-gray-900 dark:text-white">{{ selectedNode.occurrences }}</div>
          </div>
          <div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-2">关联实体</div>
            <div class="space-y-1">
              <div
                v-for="edge in connectedEdges"
                :key="edge.source + edge.target"
                class="flex items-center gap-2 text-xs p-2 rounded-lg bg-gray-50 dark:bg-gray-800"
              >
                <span class="text-gray-500">{{ edge.relation }}</span>
                <span class="font-medium text-gray-700 dark:text-gray-300">
                  {{ edge.source === selectedNode.id ? edge.target : edge.source }}
                </span>
              </div>
              <div v-if="connectedEdges.length === 0" class="text-xs text-gray-400">无关联实体</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { NInput, NButton, NIcon, NTag, useThemeVars } from 'naive-ui'
import { AddOutline, RemoveOutline, ExpandOutline, GitNetworkOutline, SearchOutline, RefreshOutline, CloseOutline, WarningOutline } from '@vicons/ionicons5'
import { useI18n } from 'vue-i18n'
import { getKnowledgeGraph, type GraphNode, type GraphEdge, type GraphData } from '@/api/knowledge'
import { apiUnwrap } from '@/utils/apiUnwrap'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'

const { t } = useI18n()
// 画布颜色随 naive 主题（NConfigProvider darkTheme / 主题覆盖）自动切换，不写死色值
const themeVars = useThemeVars()

const canvas = ref<HTMLCanvasElement>()
const canvasContainer = ref<HTMLDivElement>()
const searchQuery = ref('')
const loading = ref(false)
const loadFailed = ref(false)
const graphNodes = ref<(GraphNode & { x: number; y: number; vx: number; vy: number })[]>([])
const graphEdges = ref<GraphEdge[]>([])
const selectedNode = ref<(GraphNode & { x: number; y: number }) | null>(null)

const typeColors: Record<string, string> = {
  PERSON: '#F59E0B',
  ORGANIZATION: '#3B82F6',
  LOCATION: '#10B981',
  EVENT: '#EF4444',
  CONCEPT: '#8B5CF6',
  PRODUCT: '#EC4899',
  TECHNOLOGY: '#06B6D4',
  UNKNOWN: '#6B7280',
}

const typeLabels: Record<string, string> = {
  PERSON: '人物',
  ORGANIZATION: '组织',
  LOCATION: '地点',
  EVENT: '事件',
  CONCEPT: '概念',
  PRODUCT: '产品',
  TECHNOLOGY: '技术',
  UNKNOWN: '未知',
}

const connectedEdges = computed(() => {
  if (!selectedNode.value) return []
  return graphEdges.value.filter(
    e => e.source === selectedNode.value?.id || e.target === selectedNode.value?.id
  )
})

// Canvas state
let ctx: CanvasRenderingContext2D | null = null
let animationId = 0
let width = 0
let height = 0
let offsetX = 0
let offsetY = 0
let scale = 1
let isDragging = false
let dragNode: (typeof graphNodes.value)[0] | null = null
let mouseX = 0
let mouseY = 0

// 视图缩放与 fitView 参数
const MIN_SCALE = 0.3
const MAX_SCALE = 3
const FIT_PADDING = 48 // fitView 时节点包围盒四周留白（px）
// 标签防叠：缩放低于该阈值时隐藏节点/关系标签（密集时不叠字，缩小看全貌）
const LABEL_MIN_SCALE = 0.65

const nodeRadius = (node: { occurrences: number }) =>
  Math.max(8, Math.min(20, 8 + node.occurrences * 2))

/** 自动适配：把全部节点（含半径）框进可视区并居中 */
const fitView = () => {
  const nodes = graphNodes.value
  if (!nodes.length || width <= 0 || height <= 0) {
    offsetX = 0
    offsetY = 0
    scale = 1
    return
  }
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const node of nodes) {
    const r = nodeRadius(node)
    minX = Math.min(minX, node.x - r)
    minY = Math.min(minY, node.y - r)
    maxX = Math.max(maxX, node.x + r)
    maxY = Math.max(maxY, node.y + r)
  }
  const graphWidth = Math.max(maxX - minX, 1)
  const graphHeight = Math.max(maxY - minY, 1)
  const fitScale = Math.min(
    (width - FIT_PADDING * 2) / graphWidth,
    (height - FIT_PADDING * 2) / graphHeight
  )
  scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, fitScale))
  offsetX = width / 2 - ((minX + maxX) / 2) * scale
  offsetY = height / 2 - ((minY + maxY) / 2) * scale
}

/** 以画布内某点为锚点缩放（滚轮与缩放控件共用） */
const applyZoom = (factor: number, cx = width / 2, cy = height / 2) => {
  const newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor))
  offsetX = cx - (cx - offsetX) * (newScale / scale)
  offsetY = cy - (cy - offsetY) * (newScale / scale)
  scale = newScale
}

const zoomIn = () => applyZoom(1.2)
const zoomOut = () => applyZoom(1 / 1.2)

const initCanvas = () => {
  if (!canvas.value || !canvasContainer.value) return
  const rect = canvasContainer.value.getBoundingClientRect()
  width = rect.width
  height = rect.height
  canvas.value.width = width * window.devicePixelRatio
  canvas.value.height = height * window.devicePixelRatio
  canvas.value.style.width = width + 'px'
  canvas.value.style.height = height + 'px'
  ctx = canvas.value.getContext('2d')
  if (ctx) ctx.scale(window.devicePixelRatio, window.devicePixelRatio)
}

const fetchGraph = async () => {
  loading.value = true
  loadFailed.value = false
  try {
    const res = await getKnowledgeGraph(searchQuery.value || undefined, 80)
    // 该端点为裸结构（{nodes, edges, analytics}）：apiUnwrap 原样透传
    const data = apiUnwrap<GraphData>(res)
    if (data) {
      graphNodes.value = (data.nodes || []).map((n: GraphNode) => ({
        ...n,
        x: width / 2 + (Math.random() - 0.5) * width * 0.6,
        y: height / 2 + (Math.random() - 0.5) * height * 0.6,
        vx: 0,
        vy: 0,
      }))
      graphEdges.value = data.edges || []
    } else {
      graphNodes.value = []
      graphEdges.value = []
    }
    // 数据加载后自动适配视图，首屏完整框住节点
    fitView()
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

const simulate = () => {
  const nodes = graphNodes.value
  const alpha = 0.3
  const repulsion = 2000
  const attraction = 0.01
  const damping = 0.85
  const centerForce = 0.005

  // Repulsion between all nodes
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[j].x - nodes[i].x
      const dy = nodes[j].y - nodes[i].y
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
      const force = repulsion / (dist * dist)
      const fx = (dx / dist) * force * alpha
      const fy = (dy / dist) * force * alpha
      nodes[i].vx -= fx
      nodes[i].vy -= fy
      nodes[j].vx += fx
      nodes[j].vy += fy
    }
  }

  // Attraction along edges
  const nodeMap = new Map(nodes.map(n => [n.id, n]))
  for (const edge of graphEdges.value) {
    const source = nodeMap.get(edge.source)
    const target = nodeMap.get(edge.target)
    if (!source || !target) continue
    const dx = target.x - source.x
    const dy = target.y - source.y
    const dist = Math.sqrt(dx * dx + dy * dy)
    const force = dist * attraction * alpha
    const fx = (dx / dist) * force
    const fy = (dy / dist) * force
    source.vx += fx
    source.vy += fy
    target.vx -= fx
    target.vy -= fy
  }

  // Center gravity
  for (const node of nodes) {
    node.vx += (width / 2 - node.x) * centerForce
    node.vy += (height / 2 - node.y) * centerForce
  }

  // Apply velocities
  for (const node of nodes) {
    if (node === dragNode) continue
    node.vx *= damping
    node.vy *= damping
    node.x += node.vx
    node.y += node.vy
    // Bounds
    node.x = Math.max(40, Math.min(width - 40, node.x))
    node.y = Math.max(40, Math.min(height - 40, node.y))
  }
}

const draw = () => {
  if (!ctx) return
  // 画布取色全部走主题令牌（naive common vars），亮/暗主题自动切换
  const vars = themeVars.value
  // 标签防叠：缩小到阈值下只画图形，不画文字标签
  const showLabels = scale >= LABEL_MIN_SCALE
  ctx.clearRect(0, 0, width, height)
  ctx.save()
  ctx.translate(offsetX, offsetY)
  ctx.scale(scale, scale)

  const nodeMap = new Map(graphNodes.value.map(n => [n.id, n]))

  // Draw edges
  for (const edge of graphEdges.value) {
    const source = nodeMap.get(edge.source)
    const target = nodeMap.get(edge.target)
    if (!source || !target) continue

    ctx.beginPath()
    ctx.moveTo(source.x, source.y)
    ctx.lineTo(target.x, target.y)
    ctx.strokeStyle = vars.borderColor
    ctx.lineWidth = 1
    ctx.stroke()

    // Edge label
    if (showLabels) {
      const midX = (source.x + target.x) / 2
      const midY = (source.y + target.y) / 2
      ctx.font = '10px system-ui'
      ctx.fillStyle = vars.textColor3
      ctx.textAlign = 'center'
      ctx.fillText(edge.relation, midX, midY - 6)
    }
  }

  // Draw nodes
  for (const node of graphNodes.value) {
    const color = typeColors[node.type] || typeColors.UNKNOWN
    const radius = nodeRadius(node)
    const isSelected = selectedNode.value?.id === node.id

    // Glow for selected
    if (isSelected) {
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius + 6, 0, Math.PI * 2)
      ctx.fillStyle = color + '30'
      ctx.fill()
    }

    // Node circle
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2)
    ctx.fillStyle = color
    ctx.fill()
    ctx.strokeStyle = isSelected ? vars.textColor1 : color + '80'
    ctx.lineWidth = isSelected ? 2 : 1
    ctx.stroke()

    // Node label
    if (showLabels) {
      ctx.font = '11px system-ui'
      ctx.fillStyle = vars.textColor2
      ctx.textAlign = 'center'
      ctx.fillText(node.id.slice(0, 12), node.x, node.y + radius + 14)
    }
  }

  ctx.restore()
}

const animate = () => {
  simulate()
  draw()
  animationId = requestAnimationFrame(animate)
}

const getNodeAt = (x: number, y: number) => {
  const sx = (x - offsetX) / scale
  const sy = (y - offsetY) / scale
  for (const node of graphNodes.value) {
    const dx = node.x - sx
    const dy = node.y - sy
    const radius = nodeRadius(node)
    if (dx * dx + dy * dy < radius * radius) return node
  }
  return null
}

const onMouseDown = (e: MouseEvent) => {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const node = getNodeAt(x, y)
  if (node) {
    dragNode = node
    selectedNode.value = node
  } else {
    isDragging = true
    mouseX = x
    mouseY = y
  }
}

const onMouseMove = (e: MouseEvent) => {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  if (dragNode) {
    dragNode.x = (x - offsetX) / scale
    dragNode.y = (y - offsetY) / scale
    dragNode.vx = 0
    dragNode.vy = 0
  } else if (isDragging) {
    offsetX += x - mouseX
    offsetY += y - mouseY
    mouseX = x
    mouseY = y
  }
}

const onMouseUp = () => {
  dragNode = null
  isDragging = false
}

const onWheel = (e: WheelEvent) => {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  applyZoom(e.deltaY > 0 ? 0.9 : 1.1, x, y)
}

onMounted(async () => {
  await nextTick()
  initCanvas()
  await fetchGraph()
  animate()
})

onUnmounted(() => {
  cancelAnimationFrame(animationId)
})

watch(canvasContainer, () => {
  nextTick(initCanvas)
})
</script>
