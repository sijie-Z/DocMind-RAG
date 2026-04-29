<template>
  <div class="users-container h-full p-4 lg:p-6 overflow-y-auto">
    <!-- 现代化毛玻璃卡片容器 -->
    <div class="bg-white/70 dark:bg-gray-900/70 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 shadow-2xl shadow-blue-500/10 h-full flex flex-col overflow-hidden">
      
      <!-- 页面头部：现代化搜索与操作栏 -->
      <div class="p-6 border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-gradient-to-r from-indigo-50/30 to-indigo-50/30 dark:from-indigo-950/10 dark:to-indigo-950/10">
        <div>
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-blue-600 dark:from-blue-400 dark:to-blue-400">
            {{ t('users.title') }}
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {{ t('users.subtitle', '管理系统内的所有用户信息及其权限') }}
          </p>
        </div>
        
        <div class="flex flex-wrap items-center gap-3">
          <div class="relative group">
            <n-input
              v-model:value="searchText"
              :placeholder="t('users.searchPlaceholder')"
              clearable
              round
              class="w-64 transition-all duration-300 group-hover:shadow-lg group-hover:shadow-blue-500/10"
            >
              <template #prefix>
                <n-icon class="text-blue-500"><search-outline /></n-icon>
              </template>
            </n-input>
          </div>
          
          <n-button 
            type="primary" 
            round 
            class="shadow-lg shadow-blue-500/30 hover:shadow-blue-500/50 transition-all duration-300"
            @click="showCreateModal = true"
          >
            <template #icon>
              <n-icon><person-add-outline /></n-icon>
            </template>
            {{ t('users.create') }}
          </n-button>
          
          <n-button 
            secondary 
            circle 
            class="hover:rotate-180 transition-all duration-500"
            @click="loadUsers"
          >
            <template #icon>
              <n-icon><refresh-outline /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <!-- 表格内容区域 -->
      <div class="flex-1 p-6 overflow-hidden">
        <n-spin :show="loading" class="h-full">
          <n-data-table
            :columns="columns"
            :data="filteredUsers"
            :pagination="pagination"
            :row-key="(row) => row.id"
            :bordered="false"
            class="modern-table"
            @update:checked-row-keys="handleSelectionChange"
          />
        </n-spin>
      </div>
    </div>

    <!-- 创建/编辑用户弹窗 -->
    <n-modal 
      v-model:show="showCreateModal" 
      :title="editingUser ? t('users.edit') : t('users.create')" 
      preset="card" 
      class="max-w-xl rounded-3xl shadow-2xl"
      @after-leave="closeCreateModal"
    >
      <n-spin :show="saving">
        <n-form 
          ref="formRef" 
          :model="userForm" 
          :rules="formRules" 
          label-placement="top" 
          class="mt-4"
        >
          <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
            <n-form-item :label="t('profile.username')" path="username">
              <n-input v-model:value="userForm.username" :placeholder="t('profile.placeholder.username')" :disabled="!!editingUser" round />
            </n-form-item>
            <n-form-item :label="t('profile.nickname')" path="nickname">
              <n-input v-model:value="userForm.nickname" :placeholder="t('profile.placeholder.nickname')" round />
            </n-form-item>
            <n-form-item :label="t('profile.email')" path="email">
              <n-input v-model:value="userForm.email" :placeholder="t('profile.placeholder.email')" round />
            </n-form-item>
            <n-form-item :label="t('profile.phone')" path="phone">
              <n-input v-model:value="userForm.phone" :placeholder="t('profile.placeholder.phone')" round />
            </n-form-item>
            <n-form-item :label="t('profile.role')" path="role">
              <n-select v-model:value="userForm.role" :options="roleOptions" round />
            </n-form-item>
            <n-form-item :label="t('profile.status')" path="status">
              <n-radio-group v-model:value="userForm.status" name="status-group">
                <n-space>
                  <n-radio value="active">{{ t('users.active') }}</n-radio>
                  <n-radio value="inactive">{{ t('users.inactive') }}</n-radio>
                </n-space>
              </n-radio-group>
            </n-form-item>
          </div>
          <n-form-item :label="t('users.organizations')" path="organization_ids">
            <n-select v-model:value="userForm.organization_ids" multiple :options="organizationOptions" :placeholder="t('users.selectOrgs', '请选择所属组织')" />
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button round @click="closeCreateModal">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" round :loading="saving" @click="saveUser" class="px-8 shadow-lg shadow-blue-500/20">
            {{ t('common.save') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 重置密码弹窗 -->
    <n-modal 
      v-model:show="showResetPasswordModal" 
      :title="t('users.resetPassword')" 
      preset="card"
      class="max-w-md rounded-3xl shadow-2xl"
    >
      <n-spin :show="resettingPassword">
        <n-form 
          ref="resetPasswordFormRef" 
          :model="resetPasswordForm" 
          :rules="resetPasswordRules" 
          label-placement="top"
          class="mt-4"
        >
          <n-form-item :label="t('profile.newPassword')" path="newPassword">
            <n-input v-model:value="resetPasswordForm.newPassword" type="password" show-password-on="mousedown" round />
          </n-form-item>
          <n-form-item :label="t('profile.confirmPassword')" path="confirmPassword">
            <n-input v-model:value="resetPasswordForm.confirmPassword" type="password" show-password-on="mousedown" round />
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button round @click="showResetPasswordModal = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" round :loading="resettingPassword" @click="resetPassword" class="px-8 shadow-lg shadow-blue-500/20">
            {{ t('common.confirm') }}
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { 
  NCard, NSpace, NInput, NIcon, NButton, NSpin, NDataTable, 
  NTag, NModal, NForm, NFormItem, NSelect, NRadioButton, 
  NRadioGroup, NPopconfirm 
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  SearchOutline, RefreshOutline, CreateOutline, 
  TrashOutline, KeyOutline, PersonAddOutline
} from '@vicons/ionicons5'
import type { DataTableColumns, FormRules, FormInst } from 'naive-ui'
import { getUsers, createUser, updateUser, deleteUser, resetUserPassword } from '@/api/user'

// 类型定义
// 找到约 117 行左右
interface User {
  id: number
  username: string
  email: string
  nickname: string
  phone?: string
  role: 'admin' | 'user'
  status: 'active' | 'inactive'
  organization_ids: number[]
  organizations?: Array<{ id: number; name: string; color: string }>
  last_login_at?: string
  created_at: string
  // --- 加上下面这一行 ---
  remark?: string 
}

// 状态管理
const message = useDedupedMessage()
const { t } = useI18n()
const loading = ref(false)
const saving = ref(false)
const resettingPassword = ref(false)
const searchText = ref('')
const users = ref<User[]>([])
const selectedIds = ref<number[]>([])
const showCreateModal = ref(false)
const showResetPasswordModal = ref(false)
const editingUser = ref<User | null>(null)
const resettingUserId = ref<number | null>(null)
const formRef = ref<FormInst | null>(null)
const resetPasswordFormRef = ref<FormInst | null>(null)

// 表单数据
const userForm = ref({
  username: '',
  email: '',
  nickname: '',
  phone: '',
  role: 'user' as 'admin' | 'user',
  status: 'active' as 'active' | 'inactive',
  organization_ids: [] as number[],
  remark: ''
})

const resetPasswordForm = ref({
  newPassword: '',
  confirmPassword: ''
})

const roleOptions = [
  { label: t('users.roleUser'), value: 'user' },
  { label: t('users.roleAdmin'), value: 'admin' }
]

const organizationOptions = ref([
  { label: '技术部', value: 1 },
  { label: '产品部', value: 2 }
])

// 校验规则
const formRules = computed<FormRules>(() => ({
  username: [{ required: true, message: t('validation.required'), trigger: 'blur' }],
  email: [{ required: true, type: 'email', message: t('validation.email'), trigger: 'blur' }],
  nickname: [{ required: true, message: t('validation.required'), trigger: 'blur' }]
}))

const resetPasswordRules = computed<FormRules>(() => ({
  newPassword: [{ required: true, min: 6, message: t('validation.passwordLength'), trigger: 'blur' }],
  confirmPassword: [
    { required: true, message: t('validation.required'), trigger: 'blur' },
    {
      validator: (rule: any, value: string) => value === resetPasswordForm.value.newPassword,
      message: t('validation.passwordMismatch'),
      trigger: 'blur'
    }
  ]
}))

// 计算与分页
const filteredUsers = computed(() => {
  if (!searchText.value) return users.value
  const s = searchText.value.toLowerCase()
  return users.value.filter(u => u.username.toLowerCase().includes(s) || u.nickname.toLowerCase().includes(s))
})

const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  onChange: (page: number) => {
    pagination.page = page
    loadUsers()
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.pageSize = pageSize
    pagination.page = 1
    loadUsers()
  }
})

// 列定义
const columns: DataTableColumns<User> = [
  { type: 'selection', width: 50 },
  { title: t('profile.username'), key: 'username', width: 120, fixed: 'left' },
  { title: t('profile.nickname'), key: 'nickname', width: 120 },
  { title: t('profile.email'), key: 'email', width: 180, ellipsis: { tooltip: true } },
  { title: t('profile.phone'), key: 'phone', width: 120 },
  {
    title: t('profile.role'),
    key: 'role',
    width: 100,
    render(row) {
      return h(
        NTag,
        { type: row.role === 'admin' ? 'error' : 'info', bordered: false },
        { default: () => row.role === 'admin' ? t('users.roleAdmin') : t('users.roleUser') }
      )
    }
  },
  {
    title: t('profile.status'),
    key: 'status',
    width: 100,
    render(row) {
      return h(
        NTag,
        { type: row.status === 'active' ? 'success' : 'warning', bordered: false },
        { default: () => row.status === 'active' ? t('users.active') : t('users.inactive') }
      )
    }
  },
  {
    title: t('common.actions'),
    key: 'actions',
    width: 200,
    fixed: 'right',
    render(row) {
      return h(NSpace, null, {
        default: () => [
          h(
            NButton,
            { size: 'small', type: 'primary', secondary: true, onClick: () => handleEdit(row) },
            { default: () => t('common.edit') }
          ),
          h(
            NButton,
            { size: 'small', type: 'warning', secondary: true, onClick: () => openResetPwd(row) },
            { default: () => t('users.resetPassword') }
          ),
          h(
            NPopconfirm,
            {
              onPositiveClick: () => handleDelete(row.id)
            },
            {
              trigger: () => h(
                NButton,
                { size: 'small', type: 'error', secondary: true },
                { default: () => t('common.delete') }
              ),
              default: () => t('users.deleteConfirm')
            }
          )
        ]
      })
    }
  }
]

// 业务逻辑
const loadUsers = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.pageSize,
      search: searchText.value
    }
    const res = await getUsers(params)
    // 修复：后端返回的数据结构是 { success: true, data: { data: [], total: ... } }
    if (res.data && res.data.data) {
        if (Array.isArray(res.data.data.data)) {
            users.value = res.data.data.data
        } else {
            users.value = []
        }
        pagination.itemCount = res.data.data.total || 0
    } else {
        users.value = []
        pagination.itemCount = 0
    }
  } catch (err: any) {
    message.error('加载失败: ' + err.message)
  } finally {
    loading.value = false
  }
}

const handleEdit = (user: User) => {
  editingUser.value = user
  // 使用解构赋值并为可能为 undefined 的字段提供默认空字符串
  userForm.value = {
    username: user.username,
    email: user.email,
    nickname: user.nickname,
    phone: user.phone || '', // 如果是 undefined，就给个空字符串
    role: user.role,
    status: user.status,
    organization_ids: user.organization_ids || [],
    remark: user.remark || '' // 同样处理 remark
  }
  showCreateModal.value = true
}

const openResetPwd = (user: User) => {
  resettingUserId.value = user.id
  resetPasswordForm.value = { newPassword: '', confirmPassword: '' }
  showResetPasswordModal.value = true
}

const closeCreateModal = () => {
  showCreateModal.value = false
  editingUser.value = null
  userForm.value = { username: '', email: '', nickname: '', phone: '', role: 'user', status: 'active', organization_ids: [], remark: '' }
}

const saveUser = async () => {
  try {
    await formRef.value?.validate()
    saving.value = true
    if (editingUser.value) {
      await updateUser(editingUser.value.id, userForm.value as any)
      message.success(t('common.success'))
    } else {
      await createUser(userForm.value as any)
      message.success(t('common.success'))
    }
    closeCreateModal()
    loadUsers()
  } catch (err: any) {
    message.error(err.message || t('common.failed'))
  } finally {
    saving.value = false
  }
}

const resetPassword = async () => {
  try {
    await resetPasswordFormRef.value?.validate()
    if (!resettingUserId.value) return
    resettingPassword.value = true
    await resetUserPassword(resettingUserId.value, resetPasswordForm.value.newPassword)
    message.success(t('common.success'))
    showResetPasswordModal.value = false
  } catch (err: any) {
    message.error(err.message || t('common.failed'))
  } finally {
    resettingPassword.value = false
  }
}

const handleDelete = async (id: number) => {
  try {
    await deleteUser(id)
    message.success(t('common.success'))
    loadUsers()
  } catch (err: any) {
    message.error(err.message || t('common.failed'))
  }
}

const handleSelectionChange = (keys: any[]) => {
  selectedIds.value = keys.map(k => Number(k))
}

onMounted(loadUsers)
</script>

<style scoped>
.users-container { padding: 20px; height: 100%; }
.users-card { height: 100%; }
</style>
