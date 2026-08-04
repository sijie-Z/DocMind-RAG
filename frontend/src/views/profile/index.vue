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

                    <!-- AI Provider 设置 -->
                    <div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-xl space-y-4 mt-4">
                      <div class="font-medium text-gray-800 dark:text-white">AI 模型设置</div>
                      <div>
                        <div class="text-sm text-gray-500 mb-2">提供商</div>
                        <n-select v-model:value="providerForm.provider" :options="providerOptions" @update:value="onProviderChange" size="small" />
                      </div>
                      <div>
                        <div class="text-sm text-gray-500 mb-2">模型</div>
                        <n-select v-model:value="providerForm.model" :options="currentModelOptions" :allow-input="providerForm.provider === 'custom'" filterable size="small" />
                      </div>
                      <div v-if="currentProviderNeedsKey">
                        <div class="text-sm text-gray-500 mb-2">API Key</div>
                        <n-input v-model:value="providerForm.api_key" type="password" show-password-on="click" placeholder="sk-..." size="small" />
                      </div>
                      <div v-if="providerForm.provider === 'custom'">
                        <div class="text-sm text-gray-500 mb-2">Base URL</div>
                        <n-input v-model:value="providerForm.base_url" placeholder="https://api.example.com/v1" size="small" />
                      </div>
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
import { useProfilePage } from './composables/useProfilePage'
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
import type { UserSession, UserAuditLog } from '@/api/user'

const {
  getResponseDetail, message, dialog, t, locale, router, userStore, appStore,
  loading, loadError, loadErrorMsg, userInfo, stats, activities, sessions, auditLogs,
  sessionPagination, auditPagination, trustedDevices, sessionColumns, toggleTrust,
  showSessionDetail, auditColumns, storageUsage, storageUsedBytes, storageLimitBytes,
  storageMetricsSource, formatBytes, statItems, workflowCompletion, quickActions,
  goToRoute, themeOptions, languageOptions, handleLanguageChange, settingsForm,
  savingSettings, settingsSaveMessage, settingsSaveSuccess, providers, providerForm,
  providerOptions, currentProvider, currentModelOptions, currentProviderNeedsKey,
  onProviderChange, loadProviders, settingsChanged, handleSaveSettings, updatingProfile,
  updatingPassword, storageChartRef, activityChartRef, initCharts, handleResize,
  generatingApiKey, revokingApiKey, editFormRef, passwordFormRef, editForm, passwordForm,
  apiKey, editFormRules, passwordFormRules, getApiBaseUrl, normalizeAvatarUrl,
  handleAvatarUpload, loadUserProfile, loadUserStats, loadActivities, loadSessions,
  loadAuditLogs, formatAuditAction, handleRevokeSession, handleUpdateProfile,
  handleChangePassword, generateApiKey, revokeApiKey, copyApiKey, loadAllData, formatDate,
} = useProfilePage()
</script>

<style scoped>
/* 自定义滚动条等样式 */
</style>
