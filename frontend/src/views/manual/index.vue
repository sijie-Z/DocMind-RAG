<template>
  <div class="h-full flex flex-col bg-gray-50 dark:bg-gray-900">
    <!-- 顶部标题栏 -->
    <div class="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex justify-between items-center shadow-sm">
      <div class="flex items-center gap-3">
        <n-icon :component="BookOutline" size="24" class="text-blue-600" />
        <h1 class="text-xl font-bold text-gray-800 dark:text-white">系统操作手册</h1>
      </div>
      <div v-if="userStore.userInfo?.role === 'admin'">
        <n-button type="primary" @click="showCreateModal = true">
          <template #icon><n-icon :component="AddOutline" /></template>
          新增手册
        </n-button>
      </div>
    </div>

    <div class="flex-1 flex overflow-hidden">
      <!-- 左侧目录 -->
      <div class="w-64 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
        <div class="p-4 border-b border-gray-100 dark:border-gray-700">
          <n-input v-model:value="searchText" placeholder="搜索手册..." clearable>
            <template #prefix><n-icon :component="SearchOutline" /></template>
          </n-input>
        </div>
        <n-scrollbar class="flex-1">
          <n-menu
            :options="menuOptions"
            :value="currentManualId"
            @update:value="handleMenuSelect"
            class="manual-menu"
          />
        </n-scrollbar>
      </div>

      <!-- 右侧内容 -->
      <div class="flex-1 overflow-auto p-8 relative">
        <div v-if="currentManual" class="max-w-4xl mx-auto bg-white dark:bg-gray-800 rounded-xl shadow-sm min-h-full p-8 relative">
          <!-- 管理员操作按钮 -->
          <div v-if="userStore.userInfo?.role === 'admin'" class="absolute top-6 right-6 flex gap-2">
            <n-button size="small" secondary type="info" @click="handleEdit(currentManual)">
              <template #icon><n-icon :component="CreateOutline" /></template>
              编辑
            </n-button>
            <n-popconfirm @positive-click="handleDelete(currentManual.id)">
              <template #trigger>
                <n-button size="small" secondary type="error">
                  <template #icon><n-icon :component="TrashOutline" /></template>
                  删除
                </n-button>
              </template>
              确定要删除这篇手册吗？
            </n-popconfirm>
          </div>

          <h1 class="text-3xl font-bold text-gray-900 dark:text-white mb-4">{{ currentManual.title }}</h1>
          
          <div class="flex items-center gap-4 text-sm text-gray-500 mb-8 pb-4 border-b border-gray-100 dark:border-gray-700">
            <span class="bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 px-2 py-1 rounded text-xs">
              {{ currentManual.category }}
            </span>
            <span>更新于: {{ formatDate(currentManual.updated_at) }}</span>
          </div>

          <!-- Markdown 内容渲染 -->
          <div class="markdown-body prose dark:prose-invert max-w-none" v-html="renderedContent"></div>
        </div>

        <div v-else class="h-full flex flex-col items-center justify-center text-gray-400">
          <n-icon :component="BookOutline" size="64" class="mb-4 opacity-50" />
          <p class="text-lg">请从左侧选择查看的手册</p>
        </div>
      </div>
    </div>

    <!-- 编辑/创建弹窗 -->
    <n-modal v-model:show="showCreateModal" preset="card" :title="editingManual ? '编辑手册' : '新增手册'" class="w-[800px]">
      <n-form ref="formRef" :model="formModel" :rules="rules" label-placement="left" label-width="80">
        <n-form-item label="标题" path="title">
          <n-input v-model:value="formModel.title" placeholder="请输入手册标题" />
        </n-form-item>
        <n-form-item label="分类" path="category">
          <n-select v-model:value="formModel.category" :options="categoryOptions" tag input-props="{ autocomplete: 'off' }" placeholder="选择或输入分类" />
        </n-form-item>
        <n-form-item label="排序" path="sort_order">
          <n-input-number v-model:value="formModel.sort_order" />
        </n-form-item>
        <n-form-item label="是否发布" path="is_published">
          <n-switch v-model:value="formModel.is_published" />
        </n-form-item>
        <n-form-item label="内容" path="content">
          <n-input
            v-model:value="formModel.content"
            type="textarea"
            :autosize="{ minRows: 10, maxRows: 20 }"
            placeholder="请输入 Markdown 内容..."
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showCreateModal = false">取消</n-button>
          <n-button type="primary" @click="handleSubmit" :loading="submitting">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useUserStore } from '@/stores/user'
import { BookOutline, AddOutline, SearchOutline, CreateOutline, TrashOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { getManuals, createManual, updateManual, deleteManual, type Manual } from '@/api/manual'
import { marked } from 'marked'
import { formatDate } from '@/utils/format'
import { NIcon } from 'naive-ui'

const message = useDedupedMessage()
const userStore = useUserStore()

const manuals = ref<Manual[]>([])
const searchText = ref('')
const currentManualId = ref<number | null>(null)
const showCreateModal = ref(false)
const submitting = ref(false)
const editingManual = ref<Manual | null>(null)

const formModel = ref({
  title: '',
  content: '',
  category: 'general',
  sort_order: 0,
  is_published: true
})

const rules = {
  title: { required: true, message: '请输入标题', trigger: 'blur' },
  content: { required: true, message: '请输入内容', trigger: 'blur' },
  category: { required: true, message: '请输入分类', trigger: 'blur' }
}

const categoryOptions = [
  { label: '通用', value: 'general' },
  { label: '功能指南', value: 'guide' },
  { label: '常见问题', value: 'faq' },
  { label: 'API文档', value: 'api' }
]

// 渲染 Markdown
const renderedContent = computed(() => {
  if (!currentManual.value?.content) return ''
  return marked(currentManual.value.content)
})

const currentManual = computed(() => {
  return manuals.value.find(m => m.id === currentManualId.value)
})

// 菜单数据
const menuOptions = computed(() => {
  const filtered = manuals.value.filter(m => 
    m.title.toLowerCase().includes(searchText.value.toLowerCase()) || 
    m.category.toLowerCase().includes(searchText.value.toLowerCase())
  )
  
  // 按分类分组
  const grouped: Record<string, Manual[]> = {}
  filtered.forEach(m => {
    if (!grouped[m.category]) grouped[m.category] = []
    grouped[m.category].push(m)
  })

  return Object.keys(grouped).map(cat => ({
    label: cat === 'general' ? '通用' : (cat === 'guide' ? '功能指南' : (cat === 'faq' ? '常见问题' : cat)),
    key: cat,
    type: 'group',
    children: grouped[cat].map(m => ({
      label: m.title,
      key: m.id,
      icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) })
    }))
  }))
})

const loadManuals = async () => {
  try {
    const res = await getManuals()
    manuals.value = res.data
    if (!currentManualId.value && manuals.value.length > 0) {
      currentManualId.value = manuals.value[0].id
    }
  } catch (err) {
    message.error('加载手册失败')
  }
}

const handleMenuSelect = (key: number) => {
  currentManualId.value = key
}

const handleEdit = (manual: Manual) => {
  editingManual.value = manual
  formModel.value = {
    title: manual.title,
    content: manual.content,
    category: manual.category,
    sort_order: manual.sort_order,
    is_published: manual.is_published
  }
  showCreateModal.value = true
}

const handleDelete = async (id: number) => {
  try {
    await deleteManual(id)
    message.success('删除成功')
    await loadManuals()
    if (currentManualId.value === id) {
      currentManualId.value = manuals.value[0]?.id || null
    }
  } catch (err) {
    message.error('删除失败')
  }
}

const handleSubmit = async () => {
  if (!formModel.value.title || !formModel.value.content) {
    message.warning('请填写完整')
    return
  }
  
  try {
    submitting.value = true
    if (editingManual.value) {
      await updateManual(editingManual.value.id, formModel.value)
      message.success('更新成功')
    } else {
      await createManual(formModel.value)
      message.success('创建成功')
    }
    showCreateModal.value = false
    editingManual.value = null
    formModel.value = { title: '', content: '', category: 'general', sort_order: 0, is_published: true }
    await loadManuals()
  } catch (err) {
    message.error('操作失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadManuals()
})
</script>

<style>
/* GitHub Markdown Style Override */
.markdown-body {
  font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif,Apple Color Emoji,Segoe UI Emoji;
  font-size: 16px;
  line-height: 1.5;
  word-wrap: break-word;
}
.dark .markdown-body {
  color: #c9d1d9;
}
.markdown-body h1, .markdown-body h2 {
  border-bottom: 1px solid #eaecef;
  padding-bottom: .3em;
}
.dark .markdown-body h1, .dark .markdown-body h2 {
  border-bottom-color: #21262d;
}
.markdown-body ul {
  list-style-type: disc;
  padding-left: 2em;
}
</style>
