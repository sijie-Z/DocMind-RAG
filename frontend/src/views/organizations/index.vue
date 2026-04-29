<template>
  <div class="organizations-container h-full p-4 lg:p-6 overflow-y-auto">
    <!-- 现代化毛玻璃卡片容器 -->
    <div class="bg-white/70 dark:bg-gray-900/70 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 shadow-2xl shadow-emerald-500/10 h-full flex flex-col overflow-hidden">
      
      <!-- 页面头部：现代化搜索与操作栏 -->
      <div class="p-6 border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-gradient-to-r from-emerald-50/30 to-teal-50/30 dark:from-emerald-950/10 dark:to-teal-950/10">
        <div>
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-emerald-600 to-teal-600 dark:from-emerald-400 dark:to-teal-400">
            {{ t('org.title') }}
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {{ t('org.subtitle', '管理系统内的组织架构及其层级关系') }}
          </p>
        </div>
        
        <div class="flex flex-wrap items-center gap-3">
          <div class="relative group">
            <n-input
              v-model:value="searchText"
              :placeholder="t('org.searchPlaceholder')"
              clearable
              round
              class="w-64 transition-all duration-300 group-hover:shadow-lg group-hover:shadow-emerald-500/10"
            >
              <template #prefix>
                <n-icon class="text-emerald-500"><SearchOutline /></n-icon>
              </template>
            </n-input>
          </div>
          
          <n-button 
            v-if="!permissionDenied"
            type="primary" 
            round 
            class="shadow-lg shadow-emerald-500/30 hover:shadow-emerald-500/50 transition-all duration-300"
            @click="showCreateModal = true"
          >
            <template #icon>
              <n-icon><AddCircleOutline /></n-icon>
            </template>
            {{ t('org.create') }}
          </n-button>
          
          <n-button 
            secondary 
            circle 
            class="hover:rotate-180 transition-all duration-500"
            @click="loadOrganizations"
          >
            <template #icon>
              <n-icon><RefreshOutline /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <!-- 内容区域：左右分割布局 -->
      <div class="flex-1 p-6 overflow-hidden">
        <div v-if="permissionDenied" class="h-full flex items-center justify-center bg-white/50 dark:bg-gray-800/30 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700">
          <n-result
            status="403"
            title="当前账号无组织架构权限"
            description="游客账号默认不可访问组织架构。若需要使用，请联系管理员分配权限。"
          />
        </div>
        <n-spin :show="loading" class="h-full">
          <div v-if="!permissionDenied" class="flex h-full gap-6">
            <!-- 左侧：树形结构 -->
            <div class="w-1/3 min-w-[300px] flex flex-col bg-white/50 dark:bg-gray-800/30 rounded-2xl border border-gray-200/50 dark:border-gray-700/50 overflow-hidden">
              <div class="p-4 border-b border-gray-200/50 dark:border-gray-700/50 bg-gray-50/50 dark:bg-gray-900/50">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">{{ t('org.treeView', '架构树') }}</span>
                  <n-tooltip trigger="hover">
                    <template #trigger>
                      <n-icon class="text-gray-400 cursor-help"><HelpCircleOutline /></n-icon>
                    </template>
                    {{ t('org.dragHint', '拖拽节点可调整层级及排序') }}
                  </n-tooltip>
                </div>
                <n-input
                  v-model:value="treeSearchText"
                  :placeholder="t('org.searchTree', '快速查找...') "
                  size="small"
                  round
                >
                  <template #prefix>
                    <n-icon><SearchOutline /></n-icon>
                  </template>
                </n-input>
              </div>
              <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
                <n-tree
                  block-line
                  draggable
                  expand-on-click
                  :data="organizationTree"
                  :pattern="treeSearchText"
                  :render-label="renderTreeLabel"
                  :render-suffix="renderTreeSuffix"
                  @drop="handleDrop"
                  @update:selected-keys="(keys) => keys.length && showOrgDetails(keys[0] as number)"
                />
              </div>
            </div>

            <!-- 右侧：详细列表 -->
            <div class="flex-1 flex flex-col bg-white/50 dark:bg-gray-800/30 rounded-2xl border border-gray-200/50 dark:border-gray-700/50 overflow-hidden">
              <div class="p-4 border-b border-gray-200/50 dark:border-gray-700/50 bg-gray-50/50 dark:bg-gray-900/50 flex items-center justify-between">
                <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">{{ t('org.detailList', '详细列表') }}</span>
                <n-space>
                  <n-button v-if="selectedIds.length > 0" size="small" type="error" ghost @click="handleBatchDelete">
                    {{ t('common.batchDelete', '批量删除') }}
                  </n-button>
                </n-space>
              </div>
              <div class="flex-1 p-0 overflow-hidden">
                <n-data-table
                  :columns="columns"
                  :data="filteredOrganizations"
                  :pagination="pagination"
                  :row-key="(row) => row.id"
                  :bordered="false"
                  class="modern-table h-full"
                  @update:checked-row-keys="handleSelectionChange"
                  :row-props="(row) => ({
                    style: 'cursor: pointer;',
                    onClick: () => showOrgDetails(row.id)
                  })"
                />
              </div>
            </div>
          </div>
        </n-spin>
      </div>
    </div>

    <!-- 创建/编辑组织弹窗 -->
    <n-modal 
      v-model:show="showCreateModal" 
      :title="editingOrganization ? t('org.edit') : t('org.create')" 
      preset="card" 
      class="max-w-xl rounded-3xl shadow-2xl"
      @after-leave="closeCreateModal"
    >
      <n-spin :show="saving">
        <n-form 
          ref="formRef" 
          :model="organizationForm" 
          :rules="formRules" 
          label-placement="top" 
          class="mt-4"
        >
          <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
            <n-form-item :label="t('org.name')" path="name">
              <n-input v-model:value="organizationForm.name" :placeholder="t('org.namePlaceholder')" round />
            </n-form-item>
            
            <n-form-item :label="t('org.color')" path="color">
              <n-color-picker v-model:value="organizationForm.color" class="w-full" />
            </n-form-item>
            
            <n-form-item :label="t('org.parent')" path="parent_id">
              <n-tree-select
                v-model:value="organizationForm.parent_id"
                :options="organizationTreeOptions"
                :placeholder="t('org.parentPlaceholder')"
                clearable
              />
            </n-form-item>

            <n-form-item :label="t('org.sort')" path="sort_order">
              <n-input-number v-model:value="organizationForm.sort_order" :min="0" class="w-full" />
            </n-form-item>
          </div>
          
          <n-form-item :label="t('org.description')" path="description">
            <n-input
              v-model:value="organizationForm.description"
              type="textarea"
              :placeholder="t('org.descPlaceholder')"
              :rows="3"
              round
            />
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button round @click="showCreateModal = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" round :loading="saving" @click="saveOrganization" class="px-8 shadow-lg shadow-emerald-500/20">
            {{ t('common.save') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 组织详情侧边栏 -->
    <n-drawer v-model:show="showDetailDrawer" :width="500" placement="right" class="rounded-l-3xl">
      <n-drawer-content closable>
        <template #header>
          <div class="flex items-center gap-3">
            <div 
              :style="`width: 16px; height: 16px; border-radius: 50%; background-color: ${selectedOrg?.color}`"
            ></div>
            <span class="font-bold text-xl">{{ selectedOrg?.name }}</span>
          </div>
        </template>

        <n-spin :show="loadingDetails">
          <n-tabs type="line" animated>
            <!-- 概览 -->
            <n-tab-pane name="overview" :tab="t('org.overview', '概览')">
              <div class="space-y-6 py-4">
                <n-descriptions label-placement="left" :column="1" bordered>
                  <n-descriptions-item :label="t('org.id')">
                    {{ selectedOrg?.id }}
                  </n-descriptions-item>
                  <n-descriptions-item :label="t('org.level')">
                    {{ selectedOrg?.level }}
                  </n-descriptions-item>
                  <n-descriptions-item :label="t('org.sort')">
                    {{ selectedOrg?.sort_order }}
                  </n-descriptions-item>
                  <n-descriptions-item :label="t('org.createdAt')">
                    {{ formatDate(selectedOrg?.created_at) }}
                  </n-descriptions-item>
                </n-descriptions>
                
                <div class="bg-gray-50 dark:bg-gray-800/50 p-4 rounded-2xl">
                  <div class="text-sm font-semibold mb-2 text-gray-500">{{ t('org.description') }}</div>
                  <div class="text-gray-700 dark:text-gray-300">
                    {{ selectedOrg?.description || t('common.noDescription', '暂无描述') }}
                  </div>
                </div>
              </div>
            </n-tab-pane>

            <!-- 成员 -->
            <n-tab-pane name="members" :tab="`${t('org.members', '成员')} (${orgMembers.length})`">
              <div class="py-4">
                <n-list hoverable clickable>
                  <n-list-item v-for="member in orgMembers" :key="member.id">
                    <template #prefix>
                      <n-avatar round size="small" :style="{ backgroundColor: selectedOrg?.color }">
                        {{ member.username.charAt(0).toUpperCase() }}
                      </n-avatar>
                    </template>
                    <n-thing :title="member.nickname">
                      <template #description>
                        <n-space size="small">
                          <n-tag size="tiny" :type="member.role === 'admin' ? 'error' : 'info'">
                            {{ member.role }}
                          </n-tag>
                          <span class="text-xs text-gray-400">{{ member.email }}</span>
                        </n-space>
                      </template>
                    </n-thing>
                  </n-list-item>
                  <div v-if="orgMembers.length === 0" class="py-10 text-center text-gray-400">
                    {{ t('common.noData', '暂无成员') }}
                  </div>
                </n-list>
              </div>
            </n-tab-pane>

            <!-- 知识库 -->
            <n-tab-pane name="documents" :tab="`${t('org.documents', '知识库')} (${orgDocuments.length})`">
              <div class="py-4">
                <n-list hoverable clickable>
                  <n-list-item v-for="doc in orgDocuments" :key="doc.id">
                    <template #prefix>
                      <n-icon size="24" class="text-blue-500">
                        <DocumentTextOutline v-if="doc.file_type === 'pdf'" />
                        <DocumentOutline v-else />
                      </n-icon>
                    </template>
                    <n-thing :title="doc.title">
                      <template #description>
                        <n-space size="small">
                          <n-tag size="tiny" :type="doc.status === 'completed' ? 'success' : 'warning'">
                            {{ doc.status }}
                          </n-tag>
                          <span class="text-xs text-gray-400">{{ formatDate(doc.created_at) }}</span>
                        </n-space>
                      </template>
                    </n-thing>
                  </n-list-item>
                  <div v-if="orgDocuments.length === 0" class="py-10 text-center text-gray-400">
                    {{ t('common.noData', '暂无文档') }}
                  </div>
                </n-list>
              </div>
            </n-tab-pane>
          </n-tabs>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { 
  NCard, NSpace, NInput, NIcon, NButton, NSpin, NDataTable, 
  NModal, NForm, NFormItem, NColorPicker, NTreeSelect, NInputNumber,
  NPopconfirm, NTooltip, NTree, NDrawer, NDrawerContent,
  NTabs, NTabPane, NDescriptions, NDescriptionsItem, NList, NListItem,
  NAvatar, NThing, NTag, NResult
} from 'naive-ui'
import {
  SearchOutline, RefreshOutline, CreateOutline, 
  TrashOutline, AddCircleOutline, HelpCircleOutline,
  EllipsisHorizontalOutline, DocumentTextOutline,
  DocumentOutline, CheckmarkCircleOutline, CloseCircleOutline
} from '@vicons/ionicons5'
import type { DataTableColumns, TreeSelectOption, FormInst, TreeOption, TreeDropInfo } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { formatDate } from '@/utils/format'
import { 
  getOrganizations, 
  createOrganization, 
  updateOrganization, 
  deleteOrganization,
  batchDeleteOrganizations,
  getOrganizationTree,
  getOrganizationMembers,
  getOrganizationDocuments
} from '@/api/organization'

// 1. 定义接口
interface Organization {
  id: number
  name: string
  color: string
  description?: string
  parent_id?: number | null
  level: number
  sort_order: number
  created_at: string
  updated_at: string
  children?: Organization[]
}

// 2. 状态变量
const message = useDedupedMessage()
const { t } = useI18n()
const loading = ref(false)
const saving = ref(false)
const searchText = ref('')
const treeSearchText = ref('')
const organizations = ref<Organization[]>([])
const fullTree = ref<Organization[]>([])
const permissionDenied = ref(false)
const selectedIds = ref<number[]>([])
const showCreateModal = ref(false)
const showDetailDrawer = ref(false)
const selectedOrg = ref<Organization | null>(null)
const orgMembers = ref<any[]>([])
const orgDocuments = ref<any[]>([])
const loadingDetails = ref(false)
const editingOrganization = ref<Organization | null>(null)
const formRef = ref<FormInst | null>(null)

// 表单数据模型（统一使用 organizationForm）
const organizationForm = ref({
  name: '',
  color: '#18a058',
  description: '',
  // 将 null 改为 undefined，因为你的 API 接口定义使用了可选属性 (?) 或 undefined
  parent_id: undefined as number | undefined, 
  sort_order: 0
})

const formRules = computed(() => ({
  name: [
    { required: true, message: t('org.validation.nameRequired'), trigger: 'blur' },
    { min: 2, max: 50, message: t('org.validation.nameLength'), trigger: 'blur' }
  ],
  color: [{ required: true, message: t('org.validation.colorRequired'), trigger: 'change' }]
}))

// 3. 计算属性
const filteredOrganizations = computed(() => {
  if (!searchText.value) return organizations.value
  const search = searchText.value.toLowerCase()
  return organizations.value.filter(org => 
    org.name.toLowerCase().includes(search) || 
    org.description?.toLowerCase().includes(search)
  )
})

const organizationTree = computed(() => {
  const build = (orgs: Organization[]): TreeOption[] => {
    return orgs.map(org => ({
      label: org.name,
      key: org.id,
      color: org.color,
      children: org.children ? build(org.children) : undefined
    }))
  }
  return build(fullTree.value)
})

const renderTreeLabel = ({ option }: { option: TreeOption }) => {
  return h('div', { class: 'flex items-center gap-2' }, [
    h('div', { 
      style: `width: 8px; height: 8px; border-radius: 50%; background-color: ${option.color || '#18a058'}` 
    }),
    h('span', { class: 'text-sm' }, option.label as string)
  ])
}

const renderTreeSuffix = ({ option }: { option: TreeOption }) => {
  return h(NSpace, { size: 'small' }, {
    default: () => [
      h(NButton, { 
        quaternary: true, 
        circle: true, 
        size: 'tiny',
        onClick: (e) => {
          e.stopPropagation()
          const org = findOrgInTree(fullTree.value, option.key as number)
          if (org) handleEdit(org)
        }
      }, {
        icon: () => h(NIcon, null, { default: () => h(CreateOutline) })
      })
    ]
  })
}

const findOrgInTree = (tree: Organization[], id: number): Organization | null => {
  for (const node of tree) {
    if (node.id === id) return node
    if (node.children) {
      const found = findOrgInTree(node.children, id)
      if (found) return found
    }
  }
  return null
}

const handleDrop = async ({ node, dragNode, dropPosition }: TreeDropInfo) => {
  const dragId = dragNode.key as number
  const targetId = node.key as number
  
  let parent_id: number | undefined = undefined
  let sort_order = 0

  if (dropPosition === 'inside') {
    parent_id = targetId
    sort_order = 0
  } else {
    // Drop before or after
    const targetOrg = findOrgInTree(fullTree.value, targetId)
    if (targetOrg) {
      parent_id = targetOrg.parent_id ?? undefined
      sort_order = dropPosition === 'before' ? targetOrg.sort_order - 1 : targetOrg.sort_order + 1
    }
  }

  try {
    loading.value = true
    await updateOrganization(dragId, { parent_id, sort_order })
    message.success(t('org.adjustSuccess'))
    await loadOrganizations()
  } catch (err: any) {
    message.error(t('org.adjustFailed') + ': ' + err.message)
  } finally {
    loading.value = false
  }
}

const handleBatchDelete = async () => {
  if (selectedIds.value.length === 0) return
  try {
    loading.value = true
    await batchDeleteOrganizations(selectedIds.value)
    message.success(t('org.batchDeleteSuccess'))
    selectedIds.value = []
    await loadOrganizations()
  } catch (err: any) {
    message.error(t('org.deleteFailed') + ': ' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

const showOrgDetails = async (orgId: number) => {
  const org = findOrgInTree(fullTree.value, orgId)
  if (!org) return
  
  selectedOrg.value = org
  showDetailDrawer.value = true
  loadingDetails.value = true
  
  try {
    const [membersRes, docsRes] = await Promise.all([
      getOrganizationMembers(orgId),
      getOrganizationDocuments(orgId)
    ])
    orgMembers.value = membersRes.data.data
    orgDocuments.value = docsRes.data.data
  } catch (err: any) {
    message.error(t('org.loadDetailsFailed') + ': ' + err.message)
  } finally {
    loadingDetails.value = false
  }
}

const handleNodeClick = ({ option }: { option: TreeOption }) => {
  showOrgDetails(option.key as number)
}

const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  showQuickJumper: true,
  onChange: (page: number) => {
    pagination.page = page
    loadOrganizations()
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.pageSize = pageSize
    pagination.page = 1
    loadOrganizations()
  }
})

// 4. 表格列定义
const columns = computed<DataTableColumns<Organization>>(() => [
  { type: 'selection', width: 50 },
  {
    title: t('org.name'),
    key: 'name',
    width: 200,
    render: (row) => h('div', { style: 'display:flex; align-items:center; gap:8px' }, [
      h('div', { style: `width:12px; height:12px; border-radius:50%; background-color:${row.color}` }),
      h('span', row.name)
    ])
  },
  {
    title: t('org.level'),
    key: 'level',
    width: 80,
    align: 'center',
    render: (row) => {
      const levels = [
        t('org.level1'), 
        t('org.level2'), 
        t('org.level3'), 
        t('org.level4'), 
        t('org.level5')
      ]
      return levels[row.level - 1] || t('org.levelOther')
    }
  },
  { title: t('org.description'), key: 'description', ellipsis: { tooltip: true } },
  { title: t('org.sort'), key: 'sort_order', width: 70, align: 'center' },
  {
    title: t('org.createdAt'),
    key: 'created_at',
    width: 170,
    render: (row) => new Date(row.created_at).toLocaleString('zh-CN')
  },
  {
    title: t('common.actions'),
    key: 'actions',
    width: 120,
    fixed: 'right',
    render: (row) => h(NSpace, null, {
      default: () => [
        h(NButton, { size: 'small', quaternary: true, type: 'primary', onClick: () => handleEdit(row) }, {
          icon: () => h(NIcon, null, { default: () => h(CreateOutline) })
        }),
        h(NPopconfirm, { onPositiveClick: () => handleDelete(row.id) }, {
          trigger: () => h(NButton, { size: 'small', quaternary: true, type: 'error' }, {
            icon: () => h(NIcon, null, { default: () => h(TrashOutline) })
          }),
          default: () => t('org.deleteConfirm')
        })
      ]
    })
  }
])

// 5. 业务方法
const loadOrganizations = async () => {
  loading.value = true
  try {
    // 同时加载列表和树结构
    const [listRes, treeRes] = await Promise.all([
      getOrganizations({
        page: pagination.page,
        page_size: pagination.pageSize,
        search: searchText.value
      }),
      getOrganizationTree()
    ])

    // 处理列表数据
    if (listRes.data && listRes.data.data) {
      organizations.value = Array.isArray(listRes.data.data.data) ? listRes.data.data.data : []
      pagination.itemCount = listRes.data.data.total || 0
    }

    // 处理树结构数据
    if (treeRes.data && treeRes.data.data) {
      fullTree.value = treeRes.data.data
    }
  } catch (err: any) {
    if (err?.response?.status === 403) {
      permissionDenied.value = true
      organizations.value = []
      fullTree.value = []
      pagination.itemCount = 0
      return
    }
    message.error(t('org.loadFailed') + ': ' + err.message)
  } finally {
    loading.value = false
  }
}

// 用于弹窗选择上级组织
const organizationTreeOptions = computed((): TreeSelectOption[] => {
  const build = (orgs: Organization[]): TreeSelectOption[] => {
    return orgs.map(org => ({
      label: org.name,
      key: org.id,
      children: org.children ? build(org.children) : undefined
    }))
  }
  return build(fullTree.value)
})

const handleEdit = (row: Organization) => {
  editingOrganization.value = row
  organizationForm.value = {
    name: row.name,
    color: row.color,
    description: row.description || '',
    // 如果 row.parent_id 是 null，转换成 undefined
    parent_id: row.parent_id ?? undefined, 
    sort_order: row.sort_order
  }
  showCreateModal.value = true
}

const closeCreateModal = () => {
  editingOrganization.value = null
  organizationForm.value = { 
    name: '', 
    color: '#18a058', 
    description: '', 
    parent_id: undefined, // 这里改为 undefined
    sort_order: 0 
  }
  formRef.value?.restoreValidation()
}

const saveOrganization = async () => {
  try {
    await formRef.value?.validate()
    saving.value = true
    
    // 关键点：使用解构或者强制断言 as any 绕过 strict 类型检查
    const formData = { ...organizationForm.value }
    
    if (editingOrganization.value) {
      await updateOrganization(editingOrganization.value.id, formData as any)
      message.success(t('org.updateSuccess'))
    } else {
      await createOrganization(formData as any)
      message.success(t('org.createSuccess'))
    }
    
    showCreateModal.value = false
    await loadOrganizations()
  } catch (err: any) {
    // 只有当 err 包含 message 且不是校验失败的 error 时才报错
    if (err && err.message) {
      message.error(t('org.saveFailed') + ': ' + err.message)
    }
  } finally {
    saving.value = false
  }
}

const handleDelete = async (id: number) => {
  try {
    await deleteOrganization(id)
    message.success(t('org.deleted'))
    loadOrganizations()
  } catch (err: any) {
    message.error(t('org.deleteFailed'))
  }
}

const handleSelectionChange = (keys: (string | number)[]) => {
  selectedIds.value = keys.map(k => Number(k))
}

onMounted(loadOrganizations)
</script>

<style scoped>
.organizations-container { padding: 20px; height: 100%; box-sizing: border-box; }
.organizations-card { height: 100%; }
</style>
