<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <!-- 头部区域 -->
      <div class="mb-8">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold text-gray-800 dark:text-white">{{ t('menu.prompts') }}</h1>
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
        <template #description>
          <span>加载中...</span>
        </template>

        <!-- Skeleton loading state -->
        <div v-if="loading" class="space-y-4">
          <div v-for="n in 4" :key="n" class="bg-white dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700/50 overflow-hidden">
            <div class="p-5">
              <div class="flex items-start justify-between">
                <div class="flex-1 space-y-3">
                  <div class="flex gap-2">
                    <n-skeleton width="48px" height="22px" />
                    <n-skeleton width="60px" height="22px" />
                  </div>
                  <n-skeleton text width="70%" />
                  <n-skeleton text :repeat="2" />
                  <n-skeleton text width="40%" />
                </div>
                <div class="flex flex-col gap-2 ml-4">
                  <n-skeleton width="100px" height="28px" />
                  <div class="flex gap-1 justify-end">
                    <n-skeleton width="24px" height="24px" circle />
                    <n-skeleton width="24px" height="24px" circle />
                    <n-skeleton width="24px" height="24px" circle />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Error state -->
        <div v-if="!loading && loadError" class="py-20">
          <n-result status="error" :title="t('prompts.toast.loadFailed')" :description="loadErrorMsg">
            <template #footer>
              <n-button type="primary" round @click="loadPrompts">{{ t('common.retry') }}</n-button>
            </template>
          </n-result>
        </div>

        <!-- Empty state：筛选无命中（≠ 空库，不劝创建） -->
        <div v-else-if="!loadError && filteredPrompts.length === 0 && !loading && hasActiveFilters" class="py-20">
          <n-empty :description="t('prompts.searchEmpty')">
            <template #extra>
              <div class="space-y-3">
                <p class="text-sm text-gray-400">{{ t('prompts.searchEmptyDesc') }}</p>
                <n-button type="primary" ghost round @click="clearFilters">
                  {{ t('prompts.clearFilters') }}
                </n-button>
              </div>
            </template>
          </n-empty>
        </div>

        <!-- Empty state：空库 → 创建引导 -->
        <div v-else-if="!loadError && filteredPrompts.length === 0 && !loading" class="py-20">
          <n-empty :description="t('prompts.empty', '暂无提示词模板')">
            <template #extra>
              <n-button type="primary" round @click="handleAdd">
                <template #icon><n-icon><AddOutline /></n-icon></template>
                {{ t('prompts.create', '创建模板') }}
              </n-button>
            </template>
          </n-empty>
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
                    <n-tag v-if="item.version" size="small" round type="default" :bordered="false">
                      v{{ item.version }}
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
                    <n-button v-if="!item.is_system" size="tiny" quaternary type="error" @click="handleDelete(item.id)">
                      <template #icon><n-icon><TrashOutline /></n-icon></template>
                    </n-button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <!-- 编辑抽屉 -->
    <n-drawer v-model:show="showDrawer" :width="620" placement="right">
      <n-drawer-content :title="editingId ? '编辑模板' : '新建模板'" closable>
        <template #header-extra>
          <n-button v-if="editingId" size="tiny" quaternary @click="openVersionHistory(editingId)">
            <template #icon><n-icon><TimeOutline /></n-icon></template>
            版本历史
          </n-button>
        </template>
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
          <n-form-item label="变更说明" path="change_note" v-if="editingId">
            <n-input v-model:value="formModel.change_note" placeholder="简要说明本次变更内容（选填）" maxlength="200" show-count />
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

    <!-- Version History Drawer -->
    <n-drawer v-model:show="showVersionDrawer" :width="520" placement="right">
      <n-drawer-content title="版本历史" closable>
        <n-spin :show="versionsLoading">
          <div v-if="versions.length === 0 && !versionsLoading" class="py-10">
            <n-empty description="暂无版本历史" />
          </div>
          <div v-else class="space-y-3">
            <div
              v-for="ver in versions"
              :key="ver.id"
              class="p-4 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 transition-colors"
              :class="{ 'bg-blue-50 dark:bg-blue-900/20 border-blue-300 dark:border-blue-600': ver.version === currentVersion }"
            >
              <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                  <n-tag :type="ver.version === currentVersion ? 'success' : 'default'" size="small" round>
                    v{{ ver.version }}
                  </n-tag>
                  <span v-if="ver.version === currentVersion" class="text-xs text-green-500">当前版本</span>
                </div>
                <div class="flex gap-1">
                  <n-button
                    v-if="ver.version !== currentVersion"
                    size="tiny"
                    type="primary"
                    ghost
                    @click="restoreVersion(ver)"
                  >
                    回滚到此版本
                  </n-button>
                </div>
              </div>
              <p class="text-xs text-gray-500 mb-1">{{ formatDate(ver.created_at) }}</p>
              <p v-if="ver.change_note" class="text-sm text-gray-700 dark:text-gray-300 italic">
                「{{ ver.change_note }}」
              </p>
              <p class="text-xs text-gray-400 mt-1 line-clamp-2">{{ ver.content }}</p>
            </div>
          </div>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import { getErrorMessage } from '@/utils/apiError'
import { useI18n } from 'vue-i18n'
import { AddOutline, TrashOutline, CreateOutline, CopyOutline, SparklesOutline, SearchOutline, DocumentTextOutline, PlayOutline, TimeOutline } from '@vicons/ionicons5'
import { getPrompts, createPrompt, updatePrompt, deletePrompt, seedDefaultPrompts, getPromptVersions, restorePromptVersion, type PromptTemplate, type PromptTemplateVersion } from '@/api/prompt'

const router = useRouter()
const message = useDedupedMessage()
const dialog = useDialog()
const { t } = useI18n()

const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const submitting = ref(false)
const seeding = ref(false)
const prompts = ref<PromptTemplate[]>([])
const showDrawer = ref(false)
const editingId = ref<number | null>(null)
const activeCategory = ref<string | null>(null)
const searchQuery = ref('')

// Version history state
const showVersionDrawer = ref(false)
const versionsLoading = ref(false)
const versions = ref<PromptTemplateVersion[]>([])
const currentVersion = ref(1)

const formModel = ref({
  name: '',
  content: '',
  description: '',
  category: 'general',
  is_active: true,
  change_note: '',
})

const rules = {
  name: { required: true, message: '请输入模板名称', trigger: 'blur' },
  content: { required: true, message: '请输入提示词内容', trigger: 'blur' },
}

const categories = computed(() => [
  { label: '全部', value: null },
  { label: '通用', value: 'general' },
  { label: 'RAG', value: 'rag' },
  { label: '翻译', value: 'translate' },
  { label: '摘要', value: 'summary' },
  { label: '代码', value: 'code' },
])

const categoryTabs = computed(() => {
  const base = [
    { label: '全部', value: null, count: prompts.value.length },
    { label: '通用', value: 'general', count: prompts.value.filter(p => p.category === 'general').length },
    { label: 'RAG', value: 'rag', count: prompts.value.filter(p => p.category === 'rag').length },
    { label: '翻译', value: 'translate', count: prompts.value.filter(p => p.category === 'translate').length },
    { label: '摘要', value: 'summary', count: prompts.value.filter(p => p.category === 'summary').length },
    { label: '代码', value: 'code', count: prompts.value.filter(p => p.category === 'code').length },
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

/** E14/C1 空态分流：有搜索/分类条件时的空列表 = 筛选无命中，而非空库。 */
const hasActiveFilters = computed(
  () => searchQuery.value.trim().length > 0 || activeCategory.value !== null
)

const clearFilters = () => {
  searchQuery.value = ''
  activeCategory.value = null
}

const getCategoryLabel = (category: string) => {
  const map: Record<string, string> = { general: '通用', rag: 'RAG', translate: '翻译', summary: '摘要', code: '代码' }
  return map[category] || category
}

const formatDate = (dateStr: string) => {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const loadPrompts = async () => {
  loading.value = true
  try {
    const res = await getPrompts()
    prompts.value = res.data || []
    loadError.value = false
    loadErrorMsg.value = ''
  } catch (error) {
    // E14/D1+D2：加载失败清空旧列表，仅由页内错误态呈现（不再叠加 toast）
    loadError.value = true
    loadErrorMsg.value = getErrorMessage(error, t('prompts.toast.loadFailed'))
    prompts.value = []
  } finally {
    loading.value = false
  }
}

const handleAdd = () => {
  editingId.value = null
  formModel.value = { name: '', content: '', description: '', category: 'general', is_active: true, change_note: '' }
  showDrawer.value = true
}

const handleEdit = (item: PromptTemplate) => {
  editingId.value = item.id
  formModel.value = { name: item.name, content: item.content, description: item.description || '', category: item.category, is_active: item.is_active, change_note: '' }
  currentVersion.value = item.version || 1
  showDrawer.value = true
}

const handleCopy = (item: PromptTemplate) => {
  formModel.value = { name: item.name + ' (副本)', content: item.content, description: item.description || '', category: item.category, is_active: true, change_note: '' }
  editingId.value = null
  showDrawer.value = true
}

// 直接应用到对话 - 跳转到聊天页面并带上提示词
// W2-T7：上限与聊天页（chat/index.vue MAX_SYSTEM_PROMPT_LENGTH）对齐 5000——
// 超限在源头即明确报错，不再把词交给聊天页后被静默丢弃（第三轮终检 §2.4）。
const MAX_PROMPT_LENGTH = 5000
const handleUse = (item: PromptTemplate) => {
  if (item.content && item.content.length > MAX_PROMPT_LENGTH) {
    message.error(t('chat.promptTooLong', { max: MAX_PROMPT_LENGTH }))
    return
  }
  router.push({ name: 'Chat', query: { prompt: item.content, promptName: item.name } })
}

const handleSubmit = async () => {
  submitting.value = true
  try {
    if (editingId.value) {
      const payload: Record<string, unknown> = { ...formModel.value }
      await updatePrompt(editingId.value, payload)
      message.success(t('prompts.toast.updateSuccess'))
    } else {
      const { change_note, ...createData } = formModel.value
      await createPrompt(createData)
      message.success(t('prompts.toast.createSuccess'))
    }
    showDrawer.value = false
    loadPrompts()
  } catch (error: unknown) {
    // E14/D1：后端 detail 原文优先；axios 英文传输层文案不上屏
    message.error(getErrorMessage(error, t('prompts.toast.saveFailed')))
  } finally {
    submitting.value = false
  }
}

// Version history
const openVersionHistory = async (promptId: number) => {
  versionsLoading.value = true
  showVersionDrawer.value = true
  try {
    const res = await getPromptVersions(promptId)
    versions.value = res.data || []
    // Refresh current version from prompts list
    const current = prompts.value.find(p => p.id === promptId)
    if (current) currentVersion.value = current.version || 1
  } catch (error) {
    message.error(getErrorMessage(error, t('prompts.toast.loadVersionsFailed')))
  } finally {
    versionsLoading.value = false
  }
}

const restoreVersion = async (ver: PromptTemplateVersion) => {
  dialog.warning({
    title: '确认回滚',
    content: `确定要将模板回滚到 v${ver.version} 吗？当前内容将被保存为一个新版本。`,
    positiveText: '确认回滚',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await restorePromptVersion(ver.prompt_id, ver.version)
        message.success(`已回滚到 v${ver.version}`)
        showVersionDrawer.value = false
        loadPrompts()
      } catch (error) {
        message.error(getErrorMessage(error, t('prompts.toast.restoreFailed')))
      }
    },
  })
}

const handleDelete = (id: number) => {
  dialog.warning({
    title: t('common.confirm') || '确认删除',
    content: t('prompts.confirmDelete'),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await deletePrompt(id)
        message.success(t('prompts.toast.deleteSuccess'))
        loadPrompts()
      } catch (error) {
        message.error(getErrorMessage(error, t('prompts.toast.deleteFailed')))
      }
    },
  })
}

const handleSeed = async () => {
  seeding.value = true
  try {
    await seedDefaultPrompts()
    message.success(t('prompts.toast.seedSuccess'))
    loadPrompts()
  } catch (error) {
    message.error(getErrorMessage(error, t('prompts.toast.seedFailed')))
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
