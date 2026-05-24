<template>
  <div class="graph-page h-full min-h-0 flex flex-col bg-gray-50 dark:bg-gray-950">
    <!-- Header -->
    <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-blue-500/10 flex items-center justify-center">
          <n-icon size="20" class="text-blue-500"><GitNetworkOutline /></n-icon>
        </div>
        <div>
          <h1 class="text-base font-semibold text-gray-900 dark:text-white">知识图谱</h1>
          <p class="text-xs text-gray-500 dark:text-gray-400">知识图谱可视化</p>
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
import { NInput, NButton, NIcon, NTag } from 'naive-ui'
import { GitNetworkOutline, SearchOutline, RefreshOutline, CloseOutline } from '@vicons/ionicons5'
import { getKnowledgeGraph, type GraphNode, type GraphEdge } from '@/api/knowledge'

const canvas = ref<HTMLCanvasElement>()
const canvasContainer = ref<HTMLDivElement>()
const searchQuery = ref('')
const loading = ref(false)
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
  try {
    const res = await getKnowledgeGraph(searchQuery.value || undefined, 80)
    const data = res.data?.data || res.data
    if (data) {
      graphNodes.value = (data.nodes || []).map((n: GraphNode) => ({
        ...n,
        x: width / 2 + (Math.random() - 0.5) * width * 0.6,
        y: height / 2 + (Math.random() - 0.5) * height * 0.6,
        vx: 0,
        vy: 0,
      }))
      graphEdges.value = data.edges || []
    }
  } catch {
    // ignore
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
    ctx.strokeStyle = 'rgba(156, 163, 175, 0.3)'
    ctx.lineWidth = 1
    ctx.stroke()

    // Edge label
    const midX = (source.x + target.x) / 2
    const midY = (source.y + target.y) / 2
    ctx.font = '10px system-ui'
    ctx.fillStyle = 'rgba(156, 163, 175, 0.6)'
    ctx.textAlign = 'center'
    ctx.fillText(edge.relation, midX, midY - 6)
  }

  // Draw nodes
  for (const node of graphNodes.value) {
    const color = typeColors[node.type] || typeColors.UNKNOWN
    const radius = Math.max(8, Math.min(20, 8 + node.occurrences * 2))
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
    ctx.strokeStyle = isSelected ? '#fff' : color + '80'
    ctx.lineWidth = isSelected ? 2 : 1
    ctx.stroke()

    // Node label
    ctx.font = '11px system-ui'
    ctx.fillStyle = '#374151'
    ctx.textAlign = 'center'
    ctx.fillText(node.id.slice(0, 12), node.x, node.y + radius + 14)
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
    const radius = Math.max(8, Math.min(20, 8 + node.occurrences * 2))
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
  const delta = e.deltaY > 0 ? 0.9 : 1.1
  const newScale = Math.max(0.3, Math.min(3, scale * delta))
  offsetX = x - (x - offsetX) * (newScale / scale)
  offsetY = y - (y - offsetY) * (newScale / scale)
  scale = newScale
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
