<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
    <div class="max-w-6xl mx-auto">
      <!-- 头部 -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-800 dark:text-white">Agent 工作流</h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">可视化编排 AI 工作流，让多个模型和工具协同工作</p>
        </div>
        <div class="flex gap-2">
          <n-button @click="goToMemory">
            <template #icon><n-icon><ServerOutline /></n-icon></template>
            记忆管理
          </n-button>
          <n-button type="primary" @click="createNewWorkflow">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            新建工作流
          </n-button>
        </div>
      </div>

      <!-- 搜索栏 -->
      <div class="mb-4">
        <n-input
          v-model:value="searchKeyword"
          placeholder="搜索工作流..."
          clearable
          style="max-width: 300px"
        >
          <template #prefix>
            <n-icon><SearchOutline /></n-icon>
          </template>
        </n-input>
      </div>

      <!-- 工作流列表 -->
      <n-spin :show="loading">
        <div v-if="filteredWorkflows.length === 0" class="py-16 text-center">
          <n-icon size="48" class="text-gray-300 mb-4"><GitBranchOutline /></n-icon>
          <p class="text-gray-500 mb-4">{{ searchKeyword ? '未找到匹配的工作流' : '暂无工作流' }}</p>
          <n-button v-if="!searchKeyword" type="primary" @click="createNewWorkflow">创建第一个工作流</n-button>
        </div>

        <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div
            v-for="item in filteredWorkflows"
            :key="item.id"
            class="group bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-500 hover:shadow-lg transition-all overflow-hidden"
          >
            <!-- 节点预览 -->
            <div class="h-24 bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-700 dark:to-gray-800 flex items-center justify-center gap-2 overflow-hidden px-4">
              <template v-if="item.flow_data?.nodes?.length">
                <div
                  v-for="(node, index) in item.flow_data.nodes.slice(0, 5)"
                  :key="node.id"
                  class="w-10 h-10 rounded-lg flex items-center justify-center text-lg"
                  :class="getNodePreviewClass(node.type)"
                >
                  {{ getNodeIcon(node.type) }}
                </div>
                <div v-if="item.flow_data.nodes.length > 5" class="text-gray-400 text-sm">
                  +{{ item.flow_data.nodes.length - 5 }}
                </div>
              </template>
              <div v-else class="text-gray-400 text-sm">空白工作流</div>
            </div>

            <!-- 内容 -->
            <div class="p-4">
              <div class="flex items-start justify-between">
                <div class="flex-1 min-w-0">
                  <h3 class="font-semibold text-gray-800 dark:text-white text-lg truncate">{{ item.name }}</h3>
                  <p class="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{{ item.description || '暂无描述' }}</p>
                </div>

                <n-dropdown :options="getActionOptions(item.id)" trigger="click">
                  <n-button quaternary size="small">
                    <template #icon><n-icon><EllipsisVertical /></n-icon></template>
                  </n-button>
                </n-dropdown>
              </div>

              <!-- 元信息 -->
              <div class="flex items-center justify-between mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                <div class="flex items-center gap-3 text-xs text-gray-400">
                  <span class="flex items-center gap-1">
                    <n-icon><TimeOutline /></n-icon>
                    {{ formatDate(item.updated_at) }}
                  </span>
                  <span v-if="item.flow_data?.nodes?.length" class="flex items-center gap-1">
                    <n-icon><GitNetworkOutline /></n-icon>
                    {{ item.flow_data.nodes.length }} 节点
                  </span>
                </div>
                <n-tag v-if="item.is_active" type="success" size="small">
                  <template #icon><n-icon><CheckmarkCircleOutline /></n-icon></template>
                  启用
                </n-tag>
              </div>

              <!-- 操作按钮 -->
              <div class="flex gap-2 mt-3">
                <n-button size="small" type="primary" class="flex-1" @click="openEditor(item.id)">
                  <template #icon><n-icon><CreateOutline /></n-icon></template>
                  编辑
                </n-button>
                <n-button size="small" @click="duplicateWorkflow(item)">
                  <template #icon><n-icon><CopyOutline /></n-icon></template>
                </n-button>
              </div>
            </div>
          </div>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon, NButton, NSpin, NTag, NInput, NDropdown, useDialog, useMessage } from 'naive-ui'
import {
  AddOutline, CreateOutline, TrashOutline, GitBranchOutline, SearchOutline,
  TimeOutline, GitNetworkOutline, CheckmarkCircleOutline, EllipsisVertical, CopyOutline, ServerOutline
} from '@vicons/ionicons5'
import { getWorkflows, deleteWorkflow as deleteWorkflowApi, createWorkflow, getWorkflow } from '@/api/workflow'
import dayjs from 'dayjs'

interface WorkflowItem {
  id: number
  name: string
  description?: string
  flow_data?: { nodes: any[] }
  is_active: boolean
  updated_at: string
}

const router = useRouter()
const dialog = useDialog()
const message = useMessage()

const loading = ref(false)
const searchKeyword = ref('')
const workflows = ref<WorkflowItem[]>([])

const filteredWorkflows = computed(() => {
  if (!searchKeyword.value) return workflows.value
  const keyword = searchKeyword.value.toLowerCase()
  return workflows.value.filter(w =>
    w.name.toLowerCase().includes(keyword) ||
    (w.description?.toLowerCase().includes(keyword))
  )
})

const loadWorkflows = async () => {
  loading.value = true
  try {
    const res = await getWorkflows()
    workflows.value = res.data?.data?.items || []
  } catch (error) {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const createNewWorkflow = () => {
  router.push({ name: 'WorkflowEditor' })
}

const goToMemory = () => {
  router.push({ name: 'AgentMemory' })
}

const openEditor = (id: number) => {
  router.push({ name: 'WorkflowEditor', query: { id } })
}

const deleteWorkflow = async (id: number) => {
  dialog.warning({
    title: '确认删除',
    content: '删除后无法恢复，确定要删除此工作流吗？',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteWorkflowApi(id)
        message.success('删除成功')
        loadWorkflows()
      } catch (error) {
        message.error('删除失败')
      }
    }
  })
}

const duplicateWorkflow = async (item: WorkflowItem) => {
  try {
    const res = await createWorkflow({
      name: `${item.name} (副本)`,
      description: item.description,
      flow_data: item.flow_data as any
    })
    message.success('复制成功')
    loadWorkflows()
  } catch (error) {
    message.error('复制失败')
  }
}

const getActionOptions = (id: number) => [
  {
    label: '编辑',
    key: 'edit',
    icon: () => h(NIcon, null, { default: () => h(CreateOutline) }),
    props: {
      onClick: () => openEditor(id)
    }
  },
  {
    label: '复制',
    key: 'duplicate',
    icon: () => h(NIcon, null, { default: () => h(CopyOutline) }),
    props: {
      onClick: () => {
        const workflow = workflows.value.find(w => w.id === id)
        if (workflow) duplicateWorkflow(workflow)
      }
    }
  },
  {
    type: 'divider',
    key: 'd1'
  },
  {
    label: '删除',
    key: 'delete',
    icon: () => h(NIcon, null, { default: () => h(TrashOutline) }),
    props: {
      style: 'color: #ef4444',
      onClick: () => deleteWorkflow(id)
    }
  }
]

const getNodeIcon = (type: string) => {
  const iconMap: Record<string, string> = {
    input: '📥', output: '📤',
    llm_openai: '🤖', llm_deepseek: '🧠', llm_qwen: '💬',
    tool_search: '🔍', tool_tts: '🔊',
    condition: '🔀'
  }
  return iconMap[type] || '📦'
}

const getNodePreviewClass = (type: string) => {
  const classMap: Record<string, string> = {
    input: 'bg-blue-100 dark:bg-blue-900/30',
    output: 'bg-emerald-100 dark:bg-emerald-900/30',
    llm_openai: 'bg-green-100 dark:bg-green-900/30',
    llm_deepseek: 'bg-blue-100 dark:bg-blue-900/30',
    llm_qwen: 'bg-orange-100 dark:bg-orange-900/30',
    tool_search: 'bg-amber-100 dark:bg-amber-900/30',
    tool_tts: 'bg-pink-100 dark:bg-pink-900/30',
    condition: 'bg-cyan-100 dark:bg-cyan-900/30'
  }
  return classMap[type] || 'bg-gray-100 dark:bg-gray-700'
}

const formatDate = (date: string) => {
  return dayjs(date).format('MM-DD HH:mm')
}

onMounted(() => {
  loadWorkflows()
})
</script>