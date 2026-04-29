<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <!-- 头部区域 -->
      <div class="mb-8">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold text-gray-800 dark:text-white">{{ t('prompts.title') }}</h1>
            <p class="text-gray-500 dark:text-gray-400 mt-1 text-sm">提示词模板将直接影响AI的回答方式和风格</p>
          </div>
          <div class="flex gap-2">
            <n-button quaternary round @click="handleSeed" :loading="seeding">
              <template #icon><n-icon><SparklesOutline /></n-icon></template>
              初始化
            </n-button>
            <n-button type="primary" round @click="handleAdd">
              <template #icon><n-icon><AddOutline /></n-icon></template>
              新建模板
            </n-button>
          </div>
        </div>

        <!-- 搜索栏 -->
        <div class="mt-6 flex gap-3">
          <n-input v-model:value="searchQuery" placeholder="搜索模板..." clearable size="large" round class="flex-1">
            <template #prefix><n-icon class="text-gray-400"><SearchOutline /></n-icon></template>
          </n-input>
          <n-select
            v-model:value="activeCategory"
            :options="categories"
            placeholder="分类"
            size="large"
            style="width: 140px"
            clearable
          />
        </div>
      </div>

      <!-- 分类标签 -->
      <div class="flex gap-2 mb-6 flex-wrap">
        <n-button
          v-for="cat in categoryTabs"
          :key="cat.value"
          :type="activeCategory === cat.value ? 'primary' : 'default'"
          :ghost="activeCategory !== cat.value"
          size="small"
          round
          @click="activeCategory = cat.value"
        >
          {{ cat.label }}
          <n-badge v-if="cat.count > 0" :value="cat.count" :max="99" :show-zero="false" type="info" />
        </n-button>
      </div>

      <!-- 内容区域 -->
      <n-spin :show="loading">
        <div v-if="filteredPrompts.length === 0" class="py-20">
          <div class="text-center">
            <div class="w-20 h-20 mx-auto mb-6 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
              <n-icon size="40" class="text-blue-500"><DocumentTextOutline /></n-icon>
            </div>
            <h3 class="text-lg font-medium text-gray-700 dark:text-gray-200 mb-2">暂无提示词模板</h3>
            <p class="text-gray-400 mb-6">创建模板来定制AI的回答风格</p>
            <n-button type="primary" round @click="handleAdd">
              <template #icon><n-icon><AddOutline /></n-icon></template>
              创建模板
            </n-button>
          </div>
        </div>

        <!-- 列表 -->
        <div v-else class="space-y-4">
          <div
            v-for="item in filteredPrompts"
            :key="item.id"
            class="group bg-white dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700/50 hover:border-blue-300 dark:hover:border-blue-600 transition-all duration-200 overflow-hidden"
          >
            <div class="p-5">
              <div class="flex items-start justify-between">
                <div class="flex-1">
                  <div class="flex items-center gap-2 mb-2">
                    <n-tag :type="item.is_system ? 'info' : 'success'" size="small" round :bordered="false">
                      {{ item.is_system ? '系统' : '自定义' }}
                    </n-tag>
                    <n-tag v-if="item.category" size="small" round type="warning" :bordered="false">
                      {{ getCategoryLabel(item.category) }}
                    </n-tag>
                  </div>
                  <h3 class="font-semibold text-gray-900 dark:text-white text-lg mb-2">{{ item.name }}</h3>
                  <p class="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 leading-relaxed">{{ item.content }}</p>
                  <p v-if="item.description" class="text-xs text-gray-400 mt-2 italic">{{ item.description }}</p>
                </div>

                <div class="flex flex-col gap-2 ml-4">
                  <n-button type="primary" size="small" @click="handleUse(item)">
                    <template #icon><n-icon><PlayOutline /></n-icon></template>
                    应用到对话
                  </n-button>
                  <div class="flex gap-1">
                    <n-button size="tiny" quaternary @click="handleEdit(item)">
                      <template #icon><n-icon><CreateOutline /></n-icon></template>
                    </n-button>
                    <n-button size="tiny" quaternary @click="handleCopy(item)">
                      <template #icon><n-icon><CopyOutline /></n-icon></template>
                    </n-button>
                    <n-popconfirm v-if="!item.is_system" @positive-click="handleDelete(item.id)">
                      <template #trigger>
                        <n-button size="tiny" quaternary type="error">
                          <template #icon><n-icon><TrashOutline /></n-icon></template>
                        </n-button>
                      </template>
                      确定删除？
                    </n-popconfirm>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <!-- 编辑抽屉 -->
    <n-drawer v-model:show="showDrawer" :width="520" placement="right">
      <n-drawer-content :title="editingId ? '编辑模板' : '新建模板'" closable>
        <n-form :model="formModel" ref="formRef" :rules="rules" label-placement="top">
          <n-form-item label="模板名称" path="name">
            <n-input v-model:value="formModel.name" placeholder="给模板起个名字" maxlength="50" show-count />
          </n-form-item>
          <n-form-item label="分类" path="category">
            <n-radio-group v-model:value="formModel.category" name="categoryGroup">
              <n-space>
                <n-radio v-for="cat in categoryOptions" :key="cat.value" :value="cat.value">{{ cat.label }}</n-radio>
              </n-space>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="提示词内容" path="content">
            <n-input
              v-model:value="formModel.content"
              type="textarea"
              placeholder="输入提示词内容，这会直接影响AI的回答风格..."
              :autosize="{ minRows: 8, maxRows: 16 }"
              maxlength="5000"
              show-count
            />
          </n-form-item>
          <n-form-item label="描述" path="description">
            <n-input v-model:value="formModel.description" placeholder="可选：描述这个模板的用途" maxlength="200" show-count />
          </n-form-item>
        </n-form>
        <template #footer>
          <div class="flex gap-3">
            <n-button class="flex-1" @click="showDrawer = false">取消</n-button>
            <n-button class="flex-1" type="primary" :loading="submitting" @click="handleSubmit">
              {{ editingId ? '保存' : '创建' }}
            </n-button>
          </div>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { AddOutline, TrashOutline, CreateOutline, CopyOutline, SparklesOutline, SearchOutline, DocumentTextOutline, PlayOutline } from '@vicons/ionicons5'
import { getPrompts, createPrompt, updatePrompt, deletePrompt, seedDefaultPrompts, type PromptTemplate } from '@/api/prompt'
import dayjs from 'dayjs'

const router = useRouter()
const message = useDedupedMessage()
const { t } = useI18n()

const loading = ref(false)
const submitting = ref(false)
const seeding = ref(false)
const prompts = ref<PromptTemplate[]>([])
const showDrawer = ref(false)
const editingId = ref<number | null>(null)
const activeCategory = ref<string | null>(null)
const searchQuery = ref('')

const formModel = ref({
  name: '',
  content: '',
  description: '',
  category: 'general',
  is_active: true
})

const rules = {
  name: { required: true, message: '请输入模板名称', trigger: 'blur' },
  content: { required: true, message: '请输入提示词内容', trigger: 'blur' }
}

const categories = computed(() => [
  { label: '全部', value: null },
  { label: '通用', value: 'general' },
  { label: 'RAG', value: 'rag' },
  { label: '翻译', value: 'translate' },
  { label: '摘要', value: 'summary' },
  { label: '代码', value: 'code' }
])

const categoryTabs = computed(() => {
  const base = [
    { label: '全部', value: null, count: prompts.value.length },
    { label: '通用', value: 'general', count: prompts.value.filter(p => p.category === 'general').length },
    { label: 'RAG', value: 'rag', count: prompts.value.filter(p => p.category === 'rag').length },
    { label: '翻译', value: 'translate', count: prompts.value.filter(p => p.category === 'translate').length },
    { label: '摘要', value: 'summary', count: prompts.value.filter(p => p.category === 'summary').length },
    { label: '代码', value: 'code', count: prompts.value.filter(p => p.category === 'code').length }
  ]
  return base.filter(c => c.value === null || c.count > 0 || activeCategory.value === c.value)
})

const categoryOptions = computed(() => categories.value.filter(c => c.value !== null))

const filteredPrompts = computed(() => {
  let result = prompts.value
  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(p => p.name.toLowerCase().includes(query) || p.content.toLowerCase().includes(query))
  }
  if (activeCategory.value) {
    result = result.filter(p => p.category === activeCategory.value)
  }
  return result
})

const getCategoryLabel = (category: string) => {
  const map: Record<string, string> = { general: '通用', rag: 'RAG', translate: '翻译', summary: '摘要', code: '代码' }
  return map[category] || category
}

const loadPrompts = async () => {
  loading.value = true
  try {
    const res = await getPrompts()
    prompts.value = res.data || []
  } catch (error) {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const handleAdd = () => {
  editingId.value = null
  formModel.value = { name: '', content: '', description: '', category: 'general', is_active: true }
  showDrawer.value = true
}

const handleEdit = (item: PromptTemplate) => {
  editingId.value = item.id
  formModel.value = { name: item.name, content: item.content, description: item.description || '', category: item.category, is_active: item.is_active }
  showDrawer.value = true
}

const handleCopy = (item: PromptTemplate) => {
  formModel.value = { name: item.name + ' (副本)', content: item.content, description: item.description || '', category: item.category, is_active: true }
  editingId.value = null
  showDrawer.value = true
}

// 直接应用到对话 - 跳转到聊天页面并带上提示词
const handleUse = (item: PromptTemplate) => {
  router.push({ name: 'Chat', query: { prompt: item.content, promptName: item.name } })
}

const handleSubmit = async () => {
  submitting.value = true
  try {
    if (editingId.value) {
      await updatePrompt(editingId.value, formModel.value)
      message.success('更新成功')
    } else {
      await createPrompt(formModel.value)
      message.success('创建成功')
    }
    showDrawer.value = false
    loadPrompts()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存失败')
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (id: number) => {
  try {
    await deletePrompt(id)
    message.success('删除成功')
    loadPrompts()
  } catch (error) {
    message.error('删除失败')
  }
}

const handleSeed = async () => {
  seeding.value = true
  try {
    await seedDefaultPrompts()
    message.success('初始化成功')
    loadPrompts()
  } catch (error) {
    message.error('初始化失败')
  } finally {
    seeding.value = false
  }
}

onMounted(() => loadPrompts())
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
