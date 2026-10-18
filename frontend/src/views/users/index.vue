<template>
  <div class="users-container h-full p-4 lg:p-6 overflow-y-auto">
    <!-- 现代化毛玻璃卡片容器 -->
    <div class="bg-white/70 dark:bg-gray-900/70 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 shadow-2xl shadow-blue-500/10 h-full flex flex-col overflow-hidden">

      <!-- 页面头部：现代化搜索与操作栏 -->
      <div class="p-6 border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-gradient-to-r from-slate-50/30 to-slate-50/30 dark:from-slate-950/10 dark:to-slate-950/10">
        <div>
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-blue-600 dark:from-blue-400 dark:to-blue-400">
            {{ t('menu.users') }}
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
          <template #description>
            <span>加载中...</span>
          </template>

          <!-- Error state -->
          <div v-if="loadError" class="flex items-center justify-center h-full">
            <n-result status="error" title="加载失败" :description="loadErrorMsg">
              <template #footer>
                <n-button type="primary" round @click="loadUsers">重试</n-button>
              </template>
            </n-result>
          </div>

          <!-- Empty state -->
          <div v-else-if="!loadError && filteredUsers.length === 0 && !loading" class="flex items-center justify-center h-full">
            <n-empty :description="t('users.noData', '暂无用户数据')">
              <template #extra>
                <n-button type="primary" round @click="showCreateModal = true">{{ t('users.create') }}</n-button>
              </template>
            </n-empty>
          </div>

          <n-data-table v-else
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
            <n-form-item v-if="!editingUser" :label="t('users.initialPassword') || '初始密码'" path="password">
              <n-input v-model:value="userForm.password" type="password" show-password-on="mousedown" round />
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
          <n-form-item :label="t('users.organizations')" path="organization_id">
            <n-select v-model:value="userForm.organization_id" :options="organizationOptions" :placeholder="t('users.selectOrgs', '请选择所属组织')" />
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
import { useDialog } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import {
  NSpace, NInput, NIcon, NButton, NSpin, NDataTable,
  NTag, NModal, NForm, NFormItem, NSelect,
  NRadioGroup
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  SearchOutline, RefreshOutline,
  PersonAddOutline
} from '@vicons/ionicons5'
import type { DataTableColumns, FormRules, FormInst } from 'naive-ui'
import { getUsers, createUser, updateUser, deleteUser, resetUserPassword } from '@/api/user'
import { getOrganizations, type OrganizationListResponse } from '@/api/organization'
import { apiUnwrap } from '@/utils/apiUnwrap'

/** Safely extract error message from unknown error */
function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// 类型定义 —— 与后端 GET /users/ 列表项真实形状对齐（users.py:784-790）：
// 没有 nickname（显示名真身是 full_name，可能为 null）、没有 status（真身是布尔
// is_active）、没有 phone（列表接口不返回）。
interface User {
  id: number
  username: string
  email: string
  full_name: string | null
  organization_id: number | null
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
}

// 状态管理
const message = useDedupedMessage()
const { t } = useI18n()
const dialog = useDialog()
const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
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
  password: '',
  email: '',
  nickname: '',
  phone: '',
  role: 'user' as 'admin' | 'user',
  status: 'active' as 'active' | 'inactive',
  // T8：单组织数据模型（User.organization_id 单外键）——单选；提交时包装为
  // organization_ids（后端接口字段），多组织选择在后端已显式 400 拒收。
  organization_id: null as number | null,
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

// T8：组织选项动态拉取（此前硬编码「技术部/产品部」两条，与库内真实组织
// 毫不相干——真实组织：Default / Tester Org / tester2 的空间 / ...）。
const organizationOptions = ref<Array<{ label: string; value: number }>>([])

const loadOrganizationOptions = async () => {
  try {
    const res = await getOrganizations({ page: 1, page_size: 100 })
    // 组织列表为 {success, data: {data: [...], total, …}} 双层体 → apiUnwrap 归一
    const page = apiUnwrap<OrganizationListResponse | null>(res)
    const rows = page?.data
    organizationOptions.value = Array.isArray(rows)
      ? rows.map((org) => ({ label: org.name, value: org.id }))
      : []
  } catch {
    // 选项加载失败：下拉留空（不阻塞用户管理主流程），保存时仍可显式跳过组织
    organizationOptions.value = []
  }
}

// 校验规则
const formRules = computed<FormRules>(() => ({
  username: [{ required: true, message: t('validation.required'), trigger: 'blur' }],
  email: [{ required: true, type: 'email', message: t('validation.email'), trigger: 'blur' }],
  nickname: [{ required: true, message: t('validation.required'), trigger: 'blur' }],
  ...(editingUser.value ? {} : {
    password: [{ required: true, min: 8, message: t('validation.passwordLength'), trigger: 'blur' }]
  })
}))

const resetPasswordRules = computed<FormRules>(() => ({
  newPassword: [{ required: true, min: 8, message: t('validation.passwordLength'), trigger: 'blur' }],
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
// 注：后端 /users/ 目前只有 skip/limit，无服务端搜索 —— 此处为「当前页内」的
// 本地过滤（不再发送会被后端静默忽略的 search 参数）；全名/full_name 都可能为
// null，一律空串兜底，杜绝 undefined.toLowerCase() 崩溃。
const filteredUsers = computed(() => {
  const s = searchText.value.trim().toLowerCase()
  if (!s) return users.value
  return users.value.filter(u =>
    (u.username || '').toLowerCase().includes(s) ||
    (u.full_name || '').toLowerCase().includes(s) ||
    (u.email || '').toLowerCase().includes(s)
  )
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

// 列定义（绑定真实字段：全名 ← full_name（可空），状态 ← is_active 布尔；
// 原 phone 列因列表接口不返回该字段已移除）
const columns: DataTableColumns<User> = [
  { type: 'selection', width: 50 },
  { title: t('profile.username'), key: 'username', width: 120, fixed: 'left' },
  {
    title: t('profile.nickname'),
    key: 'full_name',
    width: 120,
    render: (row) => row.full_name || '-'
  },
  { title: t('profile.email'), key: 'email', width: 180, ellipsis: { tooltip: true } },
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
    key: 'is_active',
    width: 100,
    render(row) {
      return h(
        NTag,
        { type: row.is_active ? 'success' : 'warning', bordered: false },
        { default: () => row.is_active ? t('users.active') : t('users.inactive') }
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
            NButton,
            { size: 'small', type: 'error', secondary: true, onClick: () => handleDelete(row) },
            { default: () => t('common.delete') }
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
      // 后端真实分页参数是 skip/limit（page/page_size 会被 FastAPI 静默忽略）
      skip: (pagination.page - 1) * pagination.pageSize,
      limit: pagination.pageSize
    }
    const res = await getUsers(params)
    // 真实响应为裸扁平形状 {total, skip, limit, users: [...]}（无 success/data 信封）
    const body = res.data
    users.value = Array.isArray(body?.users) ? body.users : []
    pagination.itemCount = body?.total || 0
    loadError.value = false
  } catch (err: unknown) {
    message.error('加载失败: ' + getErrorMessage(err))
    loadError.value = true
    loadErrorMsg.value = '加载失败: ' + getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

const handleEdit = (user: User) => {
  editingUser.value = user
  // 按真实字段映射表单：显示名 ← full_name（可空），状态 ← is_active 布尔
  userForm.value = {
    username: user.username,
    password: '',
    email: user.email,
    nickname: user.full_name || '',
    phone: '',
    role: user.role,
    status: user.is_active === false ? 'inactive' : 'active',
    organization_id: user.organization_id ?? null,
    remark: ''
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
  userForm.value = { username: '', password: '', email: '', nickname: '', phone: '', role: 'user', status: 'active', organization_id: null, remark: '' }
}

const saveUser = async () => {
  try {
    await formRef.value?.validate()
    saving.value = true
    // T8：单选 organization_id → 包装为后端字段 organization_ids（单组织模型）
    const { organization_id, ...rest } = userForm.value
    const payload = { ...rest, organization_ids: organization_id != null ? [organization_id] : [] }
    if (editingUser.value) {
      const { username: _, password: __, ...updateData } = payload
      await updateUser(editingUser.value.id, updateData)
      message.success(t('common.success'))
    } else {
      await createUser(payload)
      message.success(t('common.success'))
    }
    closeCreateModal()
    loadUsers()
  } catch (err: unknown) {
    message.error(getErrorMessage(err) || t('common.failed'))
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
  } catch (err: unknown) {
    message.error(getErrorMessage(err) || t('common.failed'))
  } finally {
    resettingPassword.value = false
  }
}

const handleDelete = (row: User) => {
  dialog.warning({
    title: t('common.confirm') || '确认删除',
    content: t('users.deleteConfirmWithName', { username: row.username || row.full_name || '' }),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await deleteUser(row.id)
        message.success(t('common.success'))
        loadUsers()
      } catch (err: unknown) {
        message.error(getErrorMessage(err) || t('common.failed'))
      }
    }
  })
}

const handleSelectionChange = (keys: (string | number)[]) => {
  selectedIds.value = keys.map(k => Number(k))
}

onMounted(() => {
  loadUsers()
  loadOrganizationOptions()
})
</script>

<style scoped>
.users-container { padding: 20px; height: 100%; }
.users-card { height: 100%; }
</style>
