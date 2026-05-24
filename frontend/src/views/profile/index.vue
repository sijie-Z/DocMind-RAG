<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 pb-12 transition-colors duration-300">
    <!-- 顶部背景图 -->
    <div class="h-64 bg-gradient-to-r from-blue-500 via-blue-600 to-blue-700 relative overflow-hidden">
      <div class="absolute inset-0 bg-[url('@/assets/pattern.svg')] opacity-10"></div>
      <div class="absolute inset-0 bg-black/10"></div>
    </div>

    <n-spin :show="loading" class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 -mt-24 relative z-10">
      <template #description>
        <span>加载中...</span>
      </template>

      <!-- Error state -->
      <div v-if="loadError" class="flex items-center justify-center min-h-[400px]">
        <n-result status="error" title="加载失败" :description="loadErrorMsg">
          <template #footer>
            <n-button type="primary" round @click="loadAllData">重试</n-button>
          </template>
        </n-result>
      </div>

      <template v-if="!loadError">
      <div class="flex flex-col lg:flex-row gap-8">
        <!-- 左侧个人信息卡片 -->
        <div class="w-full lg:w-1/3 xl:w-1/4">
          <n-card class="rounded-2xl shadow-xl border-0 overflow-hidden" content-style="padding: 0;">
            <div class="p-6 text-center bg-white dark:bg-gray-800">
              <div class="relative inline-block">
                <n-avatar
                  round
                  :size="120"
                  :src="userInfo.avatar_url || userInfo.avatar || undefined"
                  class="border-4 border-white dark:border-gray-700 shadow-md bg-gradient-to-br from-blue-400 to-blue-500 text-white text-4xl font-bold"
                >
                  <template #default>
                    {{ (userInfo.nickname || userInfo.username || 'U').charAt(0).toUpperCase() }}
                  </template>
                </n-avatar>
                <div class="absolute bottom-0 right-0">
                  <n-upload
                    :custom-request="handleAvatarUpload"
                    :show-file-list="false"
                    accept="image/*"
                  >
                    <n-button circle type="primary" size="small" class="shadow-lg">
                      <template #icon><n-icon :component="CameraOutline" /></template>
                    </n-button>
                  </n-upload>
                </div>
              </div>

              <h2 class="mt-4 text-2xl font-bold text-gray-900 dark:text-white">
                {{ userInfo.nickname || userInfo.username }}
              </h2>
              <p class="text-gray-500 dark:text-gray-400 text-sm mt-1 flex items-center justify-center gap-1">
                <n-icon :component="MailOutline" /> {{ userInfo.email }}
              </p>

              <div class="mt-4 flex flex-wrap justify-center gap-2">
                <n-tag :type="userInfo.role === 'admin' ? 'error' : 'primary'" size="small" round>
                  {{ userInfo.role === 'admin' ? t('profile.roleAdmin') : t('profile.roleUser') }}
                </n-tag>
                <n-tag type="success" size="small" round v-if="userInfo.status === 'active'">
                  {{ t('users.active') }}
                </n-tag>
              </div>

              <div class="mt-6 pt-6 border-t border-gray-100 dark:border-gray-700 text-left">
                <p class="text-xs text-gray-400 uppercase font-semibold tracking-wider mb-3">
                  {{ t('profile.basicInfo') }}
                </p>
                <div class="space-y-3">
                  <div class="flex justify-between items-center text-sm">
                    <span class="text-gray-500 dark:text-gray-400">{{ t('profile.username') }}</span>
                    <span class="font-medium text-gray-800 dark:text-gray-200">{{ userInfo.username }}</span>
                  </div>
                  <div class="flex justify-between items-center text-sm">
                    <span class="text-gray-500 dark:text-gray-400">{{ t('profile.phone') }}</span>
                    <span class="font-medium text-gray-800 dark:text-gray-200">{{ userInfo.phone || t('profile.notBound') }}</span>
                  </div>
                  <div class="flex justify-between items-center text-sm">
                    <span class="text-gray-500 dark:text-gray-400">{{ t('profile.registrationTime') }}</span>
                    <span class="font-medium text-gray-800 dark:text-gray-200">{{ formatDate(userInfo.created_at) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </n-card>
        </div>

        <!-- 右侧主要内容区 -->
        <div class="w-full lg:w-2/3 xl:w-3/4">
          <n-card class="rounded-2xl shadow-xl border-0 min-h-[600px]">
            <n-tabs type="line" animated size="large" pane-class="pt-6">
              <!-- 概览面板 -->
              <n-tab-pane name="overview" :tab="t('menu.dashboard')">
                <!-- 统计卡片 -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                  <div v-for="stat in statItems" :key="stat.label" 
                       class="p-4 rounded-xl border border-gray-100 dark:border-gray-700 hover:shadow-md transition-shadow cursor-default bg-white dark:bg-gray-800"
                  >
                    <div class="flex items-center gap-3 mb-2">
                      <div :class="`p-2 rounded-lg ${stat.bgClass}`">
                        <n-icon :component="stat.icon" size="20" :class="stat.textClass" />
                      </div>
                      <span class="text-sm text-gray-500 dark:text-gray-400">{{ stat.label }}</span>
                    </div>
                    <div class="text-2xl font-bold text-gray-900 dark:text-white pl-1">
                      {{ stat.value }}
                    </div>
                  </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                  <!-- 左侧列：简介与存储 -->
                  <div class="lg:col-span-2 space-y-8">
                    <!-- 个人简介 -->
                    <div>
                      <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                        <n-icon :component="PersonOutline" class="text-blue-500" />
                        {{ t('profile.bio') }}
                      </h3>
                      <div class="p-6 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-gray-100 dark:border-gray-700 text-gray-600 dark:text-gray-300 leading-relaxed min-h-[120px]">
                        {{ userInfo.bio || t('profile.defaultBio') }}
                      </div>
                    </div>

                    <!-- 存储空间 -->
                    <div>
                      <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                        <n-icon :component="HardwareChipOutline" class="text-blue-500" />
                        {{ t('profile.storage') || '存储分析' }}
                        <span class="text-xs px-2 py-0.5 rounded-full border"
                          :class="storageMetricsSource === 'real' ? 'text-emerald-600 border-emerald-200 dark:text-emerald-400 dark:border-emerald-700' : 'text-amber-600 border-amber-200 dark:text-amber-400 dark:border-amber-700'">
                          {{ storageMetricsSource === 'real' ? t('profile.realData') : t('profile.estimatedData') }}
                        </span>
                      </h3>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-6 bg-white dark:bg-gray-800 p-6 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
                        <!-- 存储条 -->
                        <div class="space-y-6">
                          <div>
                            <div class="flex justify-between text-sm mb-2">
                              <span class="text-gray-500 dark:text-gray-400">{{ t('profile.totalProgress') || '总体进度' }}</span>
                              <span class="font-bold text-blue-600 dark:text-blue-400">{{ storageUsage }}%</span>
                            </div>
                            <n-progress
                              type="line"
                              :percentage="storageUsage"
                              :height="12"
                              :border-radius="6"
                              :color="storageUsage > 80 ? '#ef4444' : '#3b82f6'"
                              rail-color="rgba(59, 130, 246, 0.1)"
                              processing
                            />
                          </div>
                          <div class="grid grid-cols-2 gap-4">
                            <div class="p-3 bg-slate-50/50 dark:bg-slate-900/10 rounded-lg">
                              <div class="text-xs text-blue-500 font-semibold mb-1">{{ t('profile.used') || '已用' }}</div>
                              <div class="text-lg font-bold text-gray-800 dark:text-gray-200">{{ formatBytes(storageUsedBytes) }}</div>
                            </div>
                            <div class="p-3 bg-emerald-50/50 dark:bg-emerald-900/10 rounded-lg">
                              <div class="text-xs text-emerald-500 font-semibold mb-1">{{ t('profile.available') || '可用' }}</div>
                              <div class="text-lg font-bold text-gray-800 dark:text-gray-200">{{ formatBytes(storageLimitBytes - storageUsedBytes) }}</div>
                            </div>
                          </div>
                        </div>
                        
                        <!-- 饼图容器 -->
                        <div ref="storageChartRef" class="h-48"></div>
                      </div>
                    </div>

                    <!-- 活动趋势 -->
                    <div>
                      <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                        <n-icon :component="StatsChartOutline" class="text-blue-500" />
                        {{ t('profile.activityTrend') || '活动趋势' }}
                      </h3>
                      <div class="bg-white dark:bg-gray-800 p-6 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
                        <div ref="activityChartRef" class="h-64"></div>
                      </div>
                    </div>
                  </div>

                  <!-- 右侧列：账号绑定与动态 -->
                  <div class="space-y-8">
                    <!-- 最近动态 -->
                    <div>
                      <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                        <n-icon :component="TimeOutline" class="text-orange-500" />
                        {{ t('profile.recentActivity') || '最近动态' }}
                      </h3>
                      <div class="p-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
                        <n-timeline>
                          <n-timeline-item
                            v-for="activity in activities"
                            :key="activity.id"
                            :type="activity.type === 'login' ? 'success' : 'info'"
                            :title="activity.content"
                            :time="activity.time"
                          />
                        </n-timeline>
                      </div>
                    </div>

                    <div>
                      <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                        <n-icon :component="GlobeOutline" class="text-blue-500" />
                        {{ t('dashboard.quickActions') }}
                      </h3>
                      <div class="space-y-3">
                        <button
                          v-for="action in quickActions"
                          :key="action.label"
                          @click="goToRoute(action.route)"
                          class="w-full text-left p-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-slate-300 dark:hover:border-blue-500 transition-colors"
                        >
                          <div class="flex items-center justify-between">
                            <div class="flex items-center gap-3">
                              <div class="w-9 h-9 rounded-lg bg-slate-50 dark:bg-slate-900/30 flex items-center justify-center">
                                <n-icon :component="action.icon" class="text-blue-600 dark:text-blue-400" />
                              </div>
                              <div>
                                <div class="text-sm font-semibold text-gray-800 dark:text-gray-100">{{ action.label }}</div>
                                <div class="text-xs text-gray-500 dark:text-gray-400">{{ action.desc }}</div>
                              </div>
                            </div>
                            <n-icon :component="CheckmarkCircle" class="text-gray-300 dark:text-gray-600" />
                          </div>
                        </button>
                      </div>
                    </div>

                    <div class="p-5 bg-gradient-to-br from-blue-500 to-blue-600 rounded-2xl text-white">
                      <div class="text-xs opacity-80 mb-2">系统活跃度</div>
                      <div class="text-3xl font-bold">{{ workflowCompletion }}%</div>
                      <p class="text-xs mt-2 opacity-90 leading-relaxed">
                        基于近期会话、消息与知识资产综合计算，建议保持每周持续交互以提升系统使用效率。
                      </p>
                      <n-progress
                        class="mt-4"
                        type="line"
                        :percentage="workflowCompletion"
                        :show-indicator="false"
                        :height="8"
                        rail-color="rgba(255,255,255,0.25)"
                        color="#ffffff"
                      />
                    </div>
                  </div>
                </div>
              </n-tab-pane>

              <!-- 编辑资料面板 -->
              <n-tab-pane name="edit" :tab="t('profile.editProfile')">
                <div class="max-w-2xl">
                  <n-form ref="editFormRef" :model="editForm" :rules="editFormRules" label-placement="top" size="medium">
                    <n-grid :x-gap="24" :y-gap="24" :cols="2">
                      <n-form-item-gi :span="2" :label="t('profile.nickname')" path="nickname">
                        <n-input v-model:value="editForm.nickname" :placeholder="t('profile.placeholder.nickname')" />
                      </n-form-item-gi>
                      <n-form-item-gi :span="2" :label="t('profile.phone')" path="phone">
                        <n-input v-model:value="editForm.phone" :placeholder="t('profile.placeholder.phone')" />
                      </n-form-item-gi>
                      <n-form-item-gi :span="2" :label="t('profile.bio')" path="bio">
                        <n-input
                          v-model:value="editForm.bio"
                          type="textarea"
                          :placeholder="t('profile.placeholder.bio')"
                          :autosize="{ minRows: 3, maxRows: 6 }"
                        />
                      </n-form-item-gi>
                      <n-form-item-gi :span="2">
                        <n-button type="primary" @click="handleUpdateProfile" :loading="updatingProfile" class="w-full sm:w-auto px-8">
                          {{ t('common.save') }}
                        </n-button>
                      </n-form-item-gi>
                    </n-grid>
                  </n-form>
                </div>
              </n-tab-pane>

              <!-- 安全设置面板 -->
              <n-tab-pane name="security" :tab="t('profile.accountSecurity')">
                <div class="space-y-8 max-w-2xl">
                  <!-- 修改密码 -->
                  <div>
                    <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-4 flex items-center gap-2">
                      <n-icon :component="LockClosedOutline" class="text-green-500" />
                      {{ t('profile.changePassword') }}
                    </h3>
                    <n-form ref="passwordFormRef" :model="passwordForm" :rules="passwordFormRules" label-placement="top">
                      <n-grid :x-gap="24" :y-gap="24" :cols="1">
                        <n-form-item-gi :label="t('profile.currentPassword')" path="currentPassword">
                          <n-input
                            v-model:value="passwordForm.currentPassword"
                            type="password"
                            show-password-on="click"
                            :placeholder="t('profile.placeholder.oldPassword')"
                          />
                        </n-form-item-gi>
                        <n-form-item-gi :label="t('profile.newPassword')" path="newPassword">
                          <n-input
                            v-model:value="passwordForm.newPassword"
                            type="password"
                            show-password-on="click"
                            :placeholder="t('profile.placeholder.newPassword')"
                          />
                        </n-form-item-gi>
                        <n-form-item-gi :label="t('profile.confirmPassword')" path="confirmPassword">
                          <n-input
                            v-model:value="passwordForm.confirmPassword"
                            type="password"
                            show-password-on="click"
                            :placeholder="t('profile.placeholder.confirmPassword')"
                          />
                        </n-form-item-gi>
                        <n-form-item-gi>
                          <n-button type="primary" @click="handleChangePassword" :loading="updatingPassword">
                            {{ t('profile.changePassword') }}
                          </n-button>
                        </n-form-item-gi>
                      </n-grid>
                    </n-form>
                  </div>

                  <n-divider />

                  <!-- API Key 管理 -->
                  <div>
                    <h3 class="text-lg font-semibold text-gray-800 dark:text-white mb-2 flex items-center gap-2">
                      <n-icon :component="KeyOutline" class="text-orange-500" />
                      {{ t('profile.apiKey') }}
                    </h3>
                    <p class="text-sm text-gray-500 mb-4">{{ t('profile.apiKeyDesc') }}</p>
                    
                    <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
                      <div v-if="apiKey" class="flex items-center gap-2">
                        <code class="flex-1 bg-white dark:bg-gray-900 px-3 py-2 rounded border border-gray-200 dark:border-gray-700 font-mono text-sm break-all">
                          {{ apiKey }}
                        </code>
                        <n-button circle size="small" @click="copyApiKey">
                          <template #icon><n-icon :component="CopyOutline" /></template>
                        </n-button>
                      </div>
                      <div v-else class="text-gray-400 text-sm italic mb-3">
                        {{ t('profile.noApiKey') }}
                      </div>
                      
                      <div class="mt-4 flex gap-3">
                        <n-button v-if="!apiKey" type="primary" ghost size="small" @click="generateApiKey" :loading="generatingApiKey">
                          {{ t('profile.generateKey') }}
                        </n-button>
                        <template v-else>
                          <n-button type="warning" ghost size="small" @click="generateApiKey" :loading="generatingApiKey">
                            {{ t('profile.regenerateKey') }}
                          </n-button>
                          <n-button type="error" ghost size="small" @click="revokeApiKey" :loading="revokingApiKey">
                            {{ t('profile.revokeKey') }}
                          </n-button>
                        </template>
                      </div>
                    </div>
                    <p class="text-xs text-orange-500 mt-2 flex items-center gap-1">
                      <n-icon :component="WarningOutline" />
                      {{ t('profile.apiKeyWarning') }}
                    </p>
                  </div>
                </div>
              </n-tab-pane>

              <!-- 偏好设置面板 -->
              <n-tab-pane name="preferences" :tab="t('profile.preferences')">
                 <div class="max-w-2xl space-y-6">
                    <div class="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800 rounded-xl">
                      <div>
                        <div class="font-medium text-gray-800 dark:text-white">{{ t('profile.theme') }}</div>
                        <div class="text-sm text-gray-500 mt-1">{{ t('profile.themeSettingDesc') || '选择界面主题风格' }}</div>
                      </div>
                      <n-select
                        v-model:value="settingsForm.theme"
                        :options="themeOptions"
                        size="small"
                        style="width: 140px"
                      />
                    </div>

                    <div class="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800 rounded-xl">
                      <div>
                        <div class="font-medium text-gray-800 dark:text-white">{{ t('profile.language') }}</div>
                        <div class="text-sm text-gray-500 mt-1">{{ t('profile.languageSettingDesc') || '选择界面显示语言' }}</div>
                      </div>
                      <n-select 
                        v-model:value="settingsForm.language" 
                        :options="languageOptions" 
                        class="w-40" 
                      />
                    </div>

                    <div class="pt-4">
                      <n-button
                        type="primary"
                        @click="handleSaveSettings"
                        :loading="savingSettings"
                        :disabled="!settingsChanged"
                      >
                        {{ t('common.save') }}
                      </n-button>
                      <n-text v-if="settingsSaveMessage" :type="settingsSaveSuccess ? 'success' : 'error'" class="ml-4 text-sm">
                        {{ settingsSaveMessage }}
                      </n-text>
                    </div>
                 </div>
              </n-tab-pane>

              <n-tab-pane name="sessions" tab="设备管理">
                <n-data-table
                  :columns="sessionColumns"
                  :data="sessions"
                  :pagination="sessionPagination"
                  :row-key="(row: UserSession) => row.id"
                  :bordered="false"
                  size="small"
                />
              </n-tab-pane>

              <n-tab-pane name="audit" tab="操作审计">
                <n-data-table
                  :columns="auditColumns"
                  :data="auditLogs"
                  :pagination="auditPagination"
                  :row-key="(row: UserAuditLog) => row.id"
                  :bordered="false"
                  size="small"
                />
              </n-tab-pane>
            </n-tabs>
          </n-card>
        </div>
      </div>
    </template> <!-- end !loadError -->
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch, h, resolveComponent } from 'vue'
import { useDialog, NButton } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import * as echarts from 'echarts'
import {
  PersonOutline,
  MailOutline,
  ChatbubblesOutline,
  CloudUploadOutline,
  BookOutline,
  LockClosedOutline,
  KeyOutline,
  CopyOutline,
  WarningOutline,
  CameraOutline,
  TimeOutline,
  HardwareChipOutline,
  CheckmarkCircle,
  GlobeOutline,
  NotificationsOutline,
  StatsChartOutline,
  LaptopOutline,
  PhonePortraitOutline,
  TabletPortraitOutline
} from '@vicons/ionicons5'
import { getUserProfile, getUserStats, updateUserProfile, updatePassword, uploadAvatar, getUserActivities, generateApiKey as apiGenerateKey, revokeApiKey as apiRevokeKey, getMySessions, revokeMySession, getMyActivityLogs } from '@/api/user'
import type { Activity, UserSession, UserAuditLog } from '@/api/user'
import { formatDate } from '@/utils/format'
import type { UploadCustomRequestOptions } from 'naive-ui'

/** Safely extract detail message from an axios-style error response */
function getResponseDetail(error: unknown): string | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    const resp = (error as { response?: { data?: { detail?: string; message?: string } } }).response
    return resp?.data?.detail || resp?.data?.message
  }
  return undefined
}

// 接口定义
interface UserInfo {
  id: number
  username: string
  email: string
  nickname?: string
  full_name?: string
  phone?: string
  avatar?: string
  avatar_url?: string
  role: 'admin' | 'user'
  status: 'active' | 'inactive'
  bio?: string
  created_at: string
  last_login_at?: string
}

interface UserStats {
  conversationCount: number
  messageCount: number
  fileUploadCount: number
  knowledgeBaseCount: number
  storageUsed?: number
  activityTrend?: number[]
}

const message = useDedupedMessage()
const dialog = useDialog()
const { t, locale } = useI18n()
const router = useRouter()
const userStore = useUserStore()
const appStore = useAppStore()

// 页面加载状态
const loading = ref(true)
const loadError = ref(false)
const loadErrorMsg = ref('')

// 状态定义
const userInfo = ref<UserInfo>({
  id: 0,
  username: '',
  email: '',
  nickname: '',
  role: 'user',
  status: 'active',
  created_at: ''
})

const stats = reactive<UserStats>({
  conversationCount: 0,
  messageCount: 0,
  fileUploadCount: 0,
  knowledgeBaseCount: 0
})

const activities = ref<Activity[]>([])
const sessions = ref<UserSession[]>([])
const auditLogs = ref<UserAuditLog[]>([])

// 表格分页配置
const sessionPagination = reactive({
  pageSize: 5
})
const auditPagination = reactive({
  pageSize: 5
})

// 设备管理表格列
const trustedDevices = ref<Set<number>>(new Set())

const sessionColumns = [
  {
    title: '设备信息',
    key: 'device_name',
    render: (row: UserSession) => {
      const ua = row.user_agent || ''
      let DeviceIcon = LaptopOutline
      let deviceType = '桌面设备'
      if (ua.includes('Mobile') || ua.includes('Android') || ua.includes('iPhone')) {
        DeviceIcon = PhonePortraitOutline
        deviceType = '移动设备'
      } else if (ua.includes('Tablet') || ua.includes('iPad')) {
        DeviceIcon = TabletPortraitOutline
        deviceType = '平板设备'
      }
      const isTrusted = trustedDevices.value.has(row.id)
      return h('div', { class: 'flex items-center gap-3' }, [
        h('div', { class: 'w-10 h-10 rounded-xl bg-gradient-to-br from-blue-100 to-slate-100 dark:from-blue-900/30 dark:to-slate-900/30 flex items-center justify-center' }, [
          h(resolveComponent('n-icon'), { size: '22', class: 'text-blue-600 dark:text-blue-400' }, { default: () => h(DeviceIcon) })
        ]),
        h('div', {}, [
          h('div', { class: 'flex items-center gap-2' }, [
            h('span', { class: 'font-medium text-gray-800 dark:text-gray-200' }, row.device_name || deviceType),
            isTrusted ? h('span', { class: 'text-xs px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400' }, '✓ 已信任') : null
          ]),
          h('div', { class: 'text-xs text-gray-400 mt-0.5' }, ua.split(' ')[0] || '-')
        ])
      ])
    }
  },
  {
    title: 'IP地址',
    key: 'ip_address',
    width: 130,
    render: (row: UserSession) => h('div', { class: 'flex items-center gap-1' }, [
      h('code', { class: 'text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded font-mono' }, row.ip_address || '-'),
      row.ip_address ? h('n-tooltip', {}, {
        trigger: () => h('n-icon', { class: 'text-gray-400 cursor-help', size: 14 }, h('InformationCircleOutline')),
        default: () => '登录地点可能不准确，仅供参考'
      }) : null
    ])
  },
  {
    title: '状态',
    key: 'is_active',
    width: 90,
    render: (row: UserSession) => h('span', {
      class: [
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium',
        row.is_active
          ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400'
          : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
      ]
    }, [
      row.is_active ? h('span', { class: 'w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse' }) : null,
      row.is_active ? '在线' : '已下线'
    ])
  },
  {
    title: '最后活跃',
    key: 'last_seen_at',
    width: 100,
    render: (row: UserSession) => h('span', { class: 'text-xs text-gray-500' }, row.last_seen_at ? formatDate(row.last_seen_at) : '-')
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row: UserSession) => {
      const buttons = []
      const isTrusted = trustedDevices.value.has(row.id)

      // 详情按钮
      buttons.push(h(NButton, {
        size: 'small',
        quaternary: true,
        onClick: (e: Event) => { e.stopPropagation(); showSessionDetail(row) }
      }, { default: () => '详情' }))

      // 信任/取消信任
      buttons.push(h(NButton, {
        size: 'small',
        quaternary: true,
        type: isTrusted ? 'warning' : 'success',
        onClick: (e: Event) => { e.stopPropagation(); toggleTrust(row.id) }
      }, { default: () => isTrusted ? '取消信任' : '信任' }))

      // 下线按钮
      if (row.is_active && row.id) {
        buttons.push(h(NButton, {
          size: 'small',
          quaternary: true,
          type: 'error',
          onClick: (e: Event) => { e.stopPropagation(); handleRevokeSession(row.id) }
        }, { default: () => '下线' }))
      }

      return h('div', { class: 'flex gap-1 flex-wrap' }, buttons)
    }
  }
]

// 切换设备信任状态
const toggleTrust = (sessionId: number) => {
  if (trustedDevices.value.has(sessionId)) {
    trustedDevices.value.delete(sessionId)
    message.success('已取消信任此设备')
  } else {
    trustedDevices.value.add(sessionId)
    message.success('已信任此设备')
  }
  // 持久化到 localStorage
  localStorage.setItem('trustedDevices', JSON.stringify([...trustedDevices.value]))
}

// 显示会话详情
const showSessionDetail = (session: UserSession) => {
  const ua = session.user_agent || ''
  let deviceIcon = '💻'
  let deviceType = '桌面设备'
  if (ua.includes('Mobile') || ua.includes('Android') || ua.includes('iPhone')) {
    deviceIcon = '📱'
    deviceType = '移动设备'
  } else if (ua.includes('Tablet') || ua.includes('iPad')) {
    deviceIcon = '📲'
    deviceType = '平板设备'
  }

  // 解析浏览器信息
  let browser = '未知浏览器'
  if (ua.includes('Chrome')) browser = 'Chrome'
  else if (ua.includes('Firefox')) browser = 'Firefox'
  else if (ua.includes('Safari')) browser = 'Safari'
  else if (ua.includes('Edge')) browser = 'Edge'

  let os = '未知系统'
  if (ua.includes('Windows')) os = 'Windows'
  else if (ua.includes('Mac')) os = 'macOS'
  else if (ua.includes('Linux')) os = 'Linux'
  else if (ua.includes('Android')) os = 'Android'
  else if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS'

  dialog.info({
    title: '设备详情',
    content: () => h('div', { class: 'space-y-4' }, [
      // 设备概览卡片
      h('div', { class: 'flex items-center gap-4 p-4 bg-gradient-to-r from-blue-50 to-slate-50 dark:from-blue-900/20 dark:to-slate-900/20 rounded-xl' }, [
        h('div', { class: 'w-14 h-14 rounded-xl bg-white dark:bg-gray-800 flex items-center justify-center text-3xl shadow-sm' }, deviceIcon),
        h('div', {}, [
          h('div', { class: 'font-semibold text-gray-800 dark:text-white text-lg' }, session.device_name || deviceType),
          h('div', { class: 'text-sm text-gray-500 mt-0.5' }, `${browser} · ${os}`)
        ])
      ]),

      // 详细信息
      h('div', { class: 'grid grid-cols-2 gap-3 text-sm' }, [
        h('div', { class: 'p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg' }, [
          h('div', { class: 'text-xs text-gray-400 mb-1' }, 'IP地址'),
          h('div', { class: 'font-mono font-medium text-gray-700 dark:text-gray-200' }, session.ip_address || '-')
        ]),
        h('div', { class: 'p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg' }, [
          h('div', { class: 'text-xs text-gray-400 mb-1' }, '状态'),
          h('div', { class: 'font-medium' }, [
            session.is_active ? h('span', { class: 'inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400' }, [h('span', { class: 'w-2 h-2 rounded-full bg-emerald-500' }), '在线']) : h('span', { class: 'inline-flex items-center gap-1.5 text-gray-400' }, [h('span', { class: 'w-2 h-2 rounded-full bg-gray-400' }), '已下线'])
          ])
        ]),
        h('div', { class: 'p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg' }, [
          h('div', { class: 'text-xs text-gray-400 mb-1' }, '首次登录'),
          h('div', { class: 'font-medium text-gray-700 dark:text-gray-200' }, session.created_at ? formatDate(session.created_at) : '-')
        ]),
        h('div', { class: 'p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg' }, [
          h('div', { class: 'text-xs text-gray-400 mb-1' }, '最后活跃'),
          h('div', { class: 'font-medium text-gray-700 dark:text-gray-200' }, session.last_seen_at ? formatDate(session.last_seen_at) : '-')
        ])
      ]),

      // User Agent
      h('div', { class: 'mt-2' }, [
        h('div', { class: 'text-xs text-gray-400 mb-1' }, 'User Agent'),
        h('div', { class: 'text-xs text-gray-400 break-all bg-gray-50 dark:bg-gray-800/50 p-2 rounded-lg font-mono' }, ua || '-')
      ])
    ]),
    positiveText: '关闭'
  })
}

// 审计日志表格列
const auditColumns = [
  {
    title: '操作',
    key: 'action',
    render: (row: UserAuditLog) => formatAuditAction(row.action)
  },
  {
    title: '详情',
    key: 'detail',
    ellipsis: { tooltip: true },
    render: (row: UserAuditLog) => row.detail || '-'
  },
  {
    title: 'IP',
    key: 'ip_address',
    render: (row: UserAuditLog) => row.ip_address || '-'
  },
  {
    title: '时间',
    key: 'created_at',
    render: (row: UserAuditLog) => row.created_at ? formatDate(row.created_at) : '-'
  }
]

const storageUsage = ref(0) // percent
const storageUsedBytes = ref(0)
const storageLimitBytes = ref(10 * 1024 * 1024 * 1024)
const storageMetricsSource = ref<'real' | 'fallback'>('fallback')

const formatBytes = (bytes: number) => {
  const safe = Number(bytes)
  if (!Number.isFinite(safe) || safe <= 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(safe) / Math.log(k)), sizes.length - 1)
  return parseFloat((safe / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const statItems = computed(() => [
  { 
    label: t('profile.stats.conversations'), 
    value: stats.conversationCount, 
    icon: ChatbubblesOutline, 
    bgClass: 'bg-blue-100 dark:bg-blue-900/30', 
    textClass: 'text-blue-600 dark:text-blue-400' 
  },
  { 
    label: t('profile.stats.messages'), 
    value: stats.messageCount, 
    icon: MailOutline, 
    bgClass: 'bg-green-100 dark:bg-green-900/30', 
    textClass: 'text-green-600 dark:text-green-400' 
  },
  { 
    label: t('profile.stats.files'), 
    value: stats.fileUploadCount, 
    icon: CloudUploadOutline, 
    bgClass: 'bg-orange-100 dark:bg-orange-900/30', 
    textClass: 'text-orange-600 dark:text-orange-400' 
  },
  { 
    label: t('profile.stats.knowledge'), 
    value: stats.knowledgeBaseCount, 
    icon: BookOutline, 
    bgClass: 'bg-slate-100 dark:bg-slate-900/30',
    textClass: 'text-blue-600 dark:text-blue-400' 
  }
])

const workflowCompletion = computed(() => {
  const conversationScore = Math.min(stats.conversationCount * 5, 40)
  const messageScore = Math.min(Math.round(stats.messageCount / 4), 35)
  const knowledgeScore = Math.min(stats.knowledgeBaseCount * 8, 25)
  return Math.min(100, conversationScore + messageScore + knowledgeScore)
})

const quickActions = computed(() => [
  {
    label: '进入智能对话',
    desc: '继续当前任务并复用历史上下文',
    route: 'Chat',
    icon: ChatbubblesOutline
  },
  {
    label: '查看知识库',
    desc: '上传和管理团队知识文档',
    route: 'Knowledge',
    icon: BookOutline
  },
  {
    label: '系统监控',
    desc: '查看系统运行状态和性能指标',
    route: 'Monitoring',
    icon: StatsChartOutline
  },
  {
    label: '处理消息通知',
    desc: '快速查看系统提醒和处理状态',
    route: 'Notifications',
    icon: NotificationsOutline
  }
])

const goToRoute = (name: string) => {
  router.push({ name })
}

const themeOptions = [
  { label: '浅色', value: 'light' },
  { label: '深色', value: 'dark' },
  { label: '跟随系统', value: 'auto' }
]

const languageOptions = [
  { label: '简体中文', value: 'zh' },
  { label: 'English', value: 'en' },
  { label: '日本語', value: 'ja' },
  { label: 'Français', value: 'fr' }
]

const handleLanguageChange = (val: string) => {
  localStorage.setItem('language', val)
  locale.value = val
}

// Settings form
const settingsForm = reactive({
  language: userStore.settings.language,
  theme: userStore.settings.theme === 'auto' ? 'auto' : userStore.settings.theme,
})
const savingSettings = ref(false)
const settingsSaveMessage = ref('')
const settingsSaveSuccess = ref(false)

const settingsChanged = computed(() => {
  return settingsForm.language !== userStore.settings.language
    || settingsForm.theme !== userStore.settings.theme
})

const handleSaveSettings = async () => {
  savingSettings.value = true
  settingsSaveMessage.value = ''
  settingsSaveSuccess.value = false
  try {
    // Apply theme immediately
    appStore.setTheme(settingsForm.theme as 'light' | 'dark' | 'auto')
    // Apply language immediately
    locale.value = settingsForm.language
    localStorage.setItem('language', settingsForm.language)
    // Save to backend
    const result = await userStore.updateSettings({
      language: settingsForm.language,
      theme: settingsForm.theme,
    })
    if (result.success) {
      settingsSaveMessage.value = t('common.saveSuccess')
      settingsSaveSuccess.value = true
    } else {
      settingsSaveMessage.value = t('common.error')
      settingsSaveSuccess.value = false
    }
  } catch {
    settingsSaveMessage.value = t('common.error')
    settingsSaveSuccess.value = false
  } finally {
    savingSettings.value = false
    setTimeout(() => { settingsSaveMessage.value = '' }, 3000)
  }
}

// 加载状态
const updatingProfile = ref(false)
const updatingPassword = ref(false)

// ECharts
const storageChartRef = ref<HTMLElement | null>(null)
const activityChartRef = ref<HTMLElement | null>(null)
let storageChart: echarts.ECharts | null = null
let activityChart: echarts.ECharts | null = null

const initCharts = () => {
  const isDark = appStore.themeMode === 'dark'
  const textColor = isDark ? '#ccc' : '#666'
  const splitLineColor = isDark ? '#333' : '#eee'

  if (storageChartRef.value) {
    if (!storageChart) storageChart = echarts.init(storageChartRef.value)
    storageChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { 
        trigger: 'item',
        backgroundColor: isDark ? '#1f2937' : '#fff',
        borderColor: isDark ? '#374151' : '#e5e7eb',
        textStyle: { color: textColor }
      },
      legend: { 
        bottom: '0%', 
        left: 'center', 
        icon: 'circle', 
        textStyle: { color: textColor } 
      },
      series: [{
        name: '存储分布',
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 10, borderColor: isDark ? '#111827' : '#fff', borderWidth: 2 },
        label: { show: false },
        data: [
          { value: storageUsedBytes.value, name: '已用文档', itemStyle: { color: '#3b82f6' } },
          { value: Math.max(0, storageLimitBytes.value - storageUsedBytes.value), name: '可用空间', itemStyle: { color: isDark ? '#374151' : '#e2e8f0' } }
        ]
      }]
    })
  }

  if (activityChartRef.value) {
    if (!activityChart) activityChart = echarts.init(activityChartRef.value)
    const dates = Array.from({ length: 7 }, (_, i) => {
      const d = new Date()
      d.setDate(d.getDate() - (6 - i))
      return d.toLocaleDateString(locale.value === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric' })
    })
    
    activityChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { 
        trigger: 'axis',
        backgroundColor: isDark ? '#1f2937' : '#fff',
        borderColor: isDark ? '#374151' : '#e5e7eb',
        textStyle: { color: textColor }
      },
      grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: dates,
        axisLine: { lineStyle: { color: splitLineColor } },
        axisLabel: { color: textColor }
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { type: 'dashed', color: splitLineColor } },
        axisLabel: { color: textColor }
      },
      series: [{
        name: '活跃度',
        type: 'line',
        smooth: true,
        data: stats.activityTrend || [0, 0, 0, 0, 0, 0, 0],
        symbol: 'circle',
        symbolSize: 8,
        itemStyle: { color: '#3b82f6' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
            { offset: 1, color: 'rgba(59, 130, 246, 0)' }
          ])
        }
      }]
    })
  }
}

// Watch theme change
watch(() => appStore.themeMode, () => {
  if (storageChart || activityChart) {
    storageChart?.dispose()
    activityChart?.dispose()
    storageChart = null
    activityChart = null
    setTimeout(initCharts, 100)
  }
})

// Watch stats data change to update activity chart
watch(() => stats.activityTrend, () => {
  if (activityChart) initCharts()
}, { deep: true })

const handleResize = () => {
  storageChart?.resize()
  activityChart?.resize()
}
const generatingApiKey = ref(false)
const revokingApiKey = ref(false)

// 表单引用
const editFormRef = ref()
const passwordFormRef = ref()

// 表单数据
const editForm = ref({
  nickname: '',
  phone: '',
  bio: ''
})

const passwordForm = ref({
  currentPassword: '',
  newPassword: '',
  confirmPassword: ''
})

const apiKey = ref('')

// 校验规则
const editFormRules = {
  nickname: [
    { required: true, message: t('profile.placeholder.nickname'), trigger: 'blur' },
    { min: 2, max: 50, message: t('profile.validation.nicknameLength'), trigger: 'blur' }
  ],
  phone: [
    { pattern: /^1[3-9]\d{9}$/, message: t('profile.validation.phoneInvalid'), trigger: 'blur' }
  ]
}

const passwordFormRules = {
  currentPassword: [
    { required: true, message: t('profile.placeholder.oldPassword'), trigger: 'blur' }
  ],
  newPassword: [
    { required: true, message: t('profile.placeholder.newPassword'), trigger: 'blur' },
    { min: 6, max: 20, message: t('profile.validation.passwordLength'), trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: t('profile.placeholder.confirmPassword'), trigger: 'blur' },
    {
      validator: (_rule: any, value: string) => {
        return value === passwordForm.value.newPassword
      },
      message: t('profile.passwordMismatch'),
      trigger: 'blur'
    }
  ]
}

// 头像上传
const getApiBaseUrl = () => import.meta.env.VITE_API_BASE_URL || ''

const normalizeAvatarUrl = (rawUrl?: string) => {
  if (!rawUrl) return ''
  const clean = rawUrl.replace(/\\/g, '/')
  const apiBase = getApiBaseUrl()

  if (clean.includes('/files/')) {
    const idx = clean.indexOf('/files/')
    return `${apiBase}${clean.slice(idx)}?t=${Date.now()}`
  }

  if (clean.startsWith('http://') || clean.startsWith('https://')) {
    return `${clean}?t=${Date.now()}`
  }

  return `${apiBase}/${clean.replace(/^\/+/, '')}?t=${Date.now()}`
}

const handleAvatarUpload = async ({ file, onFinish, onError }: UploadCustomRequestOptions) => {
  try {
    const response = await uploadAvatar(file.file as File)
    const resData = response.data as Record<string, unknown>
    const nestedData = resData?.data as Record<string, unknown> | undefined
    const rawUrl = (resData?.url || nestedData?.url || nestedData) as string

    if (!rawUrl) {
      throw new Error('avatar url empty')
    }

    const finalUrl = normalizeAvatarUrl(rawUrl)

    await updateUserProfile({ avatar: finalUrl })

    if (userInfo.value) {
      userInfo.value.avatar = finalUrl
      userInfo.value.avatar_url = finalUrl
    }

    userStore.updateUserInfo({
      avatar: finalUrl,
      avatar_url: finalUrl
    })

    message.success(t('profile.upload.success'))
    onFinish()
  } catch (error) {
    onError()
    message.error(t('profile.upload.failed'))
  }
}

// 数据加载
const loadUserProfile = async () => {
  try {
    const response = await getUserProfile()
    const data = response.data
    if (data) {
      userInfo.value = {
        ...data,
        nickname: data.nickname || data.full_name || data.username,
        avatar: data.avatar_url
      }
      
      // 设置 API Key
      if (data.api_key) {
        apiKey.value = data.api_key
      }
      
      // 初始化编辑表单
      editForm.value = {
        nickname: userInfo.value.nickname || '',
        phone: userInfo.value.phone || '',
        bio: userInfo.value.bio || ''
      }
    }
  } catch (error) {
    // Failed to load profile
    message.error(t('profile.loadUserFailed'))
  }
}

const loadUserStats = async () => {
  try {
    const response = await getUserStats()
    const data = response.data
    if (data) {
      stats.conversationCount = data.conversation_count || 0
      stats.messageCount = data.message_count || 0
      stats.fileUploadCount = data.file_count || 0
      stats.knowledgeBaseCount = data.knowledge_count || 0
      stats.activityTrend = data.activity_trend || [0, 0, 0, 0, 0, 0, 0]

      storageUsedBytes.value = Number(data.storage_used || 0)
      storageLimitBytes.value = Number(data.storage_limit || storageLimitBytes.value || 1)
      if (!Number.isFinite(storageLimitBytes.value) || storageLimitBytes.value <= 0) {
        storageLimitBytes.value = 10 * 1024 * 1024 * 1024
      }
      storageMetricsSource.value = data.storage_limit != null ? 'real' : 'fallback'
      const percentage = Math.round((storageUsedBytes.value / storageLimitBytes.value) * 100)
      storageUsage.value = Math.min(percentage, 100)
    }
  } catch (error) {
    // Failed to load stats
    // 静默失败，显示0
  }
}

const loadActivities = async () => {
  try {
    const response = await getUserActivities()
    if (response.data) {
      activities.value = response.data.map(item => ({
        ...item,
        time: formatDate(item.time)
      }))
    }
  } catch (error) {
    // Failed to load activities
  }
}

const loadSessions = async () => {
  try {
    const response = await getMySessions()
    sessions.value = response.data || []
  } catch (error) {
    // Failed to load sessions
  }
}

const loadAuditLogs = async () => {
  try {
    const response = await getMyActivityLogs()
    auditLogs.value = response.data || []
  } catch (error) {
    // Failed to load audit logs
  }
}

const formatAuditAction = (action?: string) => {
  const mapping: Record<string, string> = {
    login: '登录系统',
    profile_updated: '更新个人资料',
    password_changed: '修改密码',
    api_key_generated: '生成 API Key',
    api_key_revoked: '撤销 API Key',
    session_revoked: '下线设备',
  }
  return mapping[action || ''] || action || '未知操作'
}

const handleRevokeSession = async (sessionId: number) => {
  if (!sessionId) return
  try {
    await revokeMySession(sessionId)
    message.success(t('profile.sessionRevoked') || '设备已下线')
    await Promise.all([loadSessions(), loadAuditLogs()])
  } catch (error) {
    message.error(t('profile.revokeFailed') || '下线失败')
  }
}

// 业务逻辑
const handleUpdateProfile = async () => {
  try {
    await editFormRef.value?.validate()
    updatingProfile.value = true
    
    await updateUserProfile({
      nickname: editForm.value.nickname,
      phone: editForm.value.phone,
      bio: editForm.value.bio
    })
    
    message.success(t('common.saveSuccess'))
    await loadUserProfile()
  } catch (error: unknown) {
    const errMsg = getResponseDetail(error)
    if (!errMsg) {
      message.error(t('profile.updateFailed'))
    }
  } finally {
    updatingProfile.value = false
  }
}

const handleChangePassword = async () => {
  try {
    await passwordFormRef.value?.validate()
    updatingPassword.value = true
    
    await updatePassword({
      old_password: passwordForm.value.currentPassword,
      new_password: passwordForm.value.newPassword
    })
    
    message.success(t('profile.passwordChangeSuccess'))
    passwordForm.value = { currentPassword: '', newPassword: '', confirmPassword: '' }
  } catch (error: unknown) {
    const errMsg = getResponseDetail(error) || t('profile.passwordChangeFailed')
    message.error(errMsg)
  } finally {
    updatingPassword.value = false
  }
}

// API Key 逻辑
const generateApiKey = async () => {
  try {
    generatingApiKey.value = true
    const response = await apiGenerateKey()
    const resData = response.data as Record<string, unknown>
    if (resData?.api_key) {
      apiKey.value = resData.api_key as string
      message.success(t('profile.apiKeyGenerated'))
    }
  } catch (error: unknown) {
    const errMsg = getResponseDetail(error) || t('profile.apiKeyGenerateFailed')
    message.error(errMsg)
  } finally {
    generatingApiKey.value = false
  }
}

const revokeApiKey = async () => {
  try {
    revokingApiKey.value = true
    await apiRevokeKey()
    apiKey.value = ''
    message.success(t('profile.apiKeyRevoked'))
  } catch (error: unknown) {
    const errMsg = getResponseDetail(error) || t('profile.apiKeyRevokeFailed') || '撤销 API Key 失败'
    message.error(errMsg)
  } finally {
    revokingApiKey.value = false
  }
}

const copyApiKey = () => {
  if (!apiKey.value) return
  navigator.clipboard.writeText(apiKey.value)
  message.success(t('common.copySuccess'))
}

const loadAllData = async () => {
  loadError.value = false
  loading.value = true
  try {
    // 加载已信任的设备
    try {
      const savedTrusted = localStorage.getItem('trustedDevices')
      if (savedTrusted) {
        trustedDevices.value = new Set(JSON.parse(savedTrusted))
      }
    } catch (e) {
      // Failed to load trusted devices
    }

    await loadUserProfile()
    await loadUserStats()
    await loadActivities()
    await loadSessions()
    await loadAuditLogs()

    loadError.value = false
  } catch (error) {
    loadError.value = true
    loadErrorMsg.value = t('profile.loadFailed', '加载个人资料失败')
  } finally {
    loading.value = false
  }

  // Wait for next tick to ensure DOM is ready for ECharts
  setTimeout(() => {
    initCharts()
    window.addEventListener('resize', handleResize)
  }, 100)
}

onMounted(loadAllData)

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  storageChart?.dispose()
  activityChart?.dispose()
})
</script>

<style scoped>
/* 自定义滚动条等样式 */
</style>
