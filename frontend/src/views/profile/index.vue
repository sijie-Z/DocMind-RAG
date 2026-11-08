<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 pb-12 transition-colors duration-300">
    <!-- 顶部背景图 -->
    <div class="h-64 bg-gradient-to-r from-blue-500 via-blue-600 to-blue-700 relative overflow-hidden">
      <!-- 注：此处原引用 @/assets/pattern.svg，但该文件从未存在于仓库中
           （vite 5 + unocss 0.58 容忍了悬空引用，升级到 vite 6 + unocss 0.65 后
           构建会真正解析它并报 ENOENT）。已移除该死引用，视觉结果不变。
           如需图案底纹，请补上 src/assets/pattern.svg 后恢复本行。 -->
      <div class="absolute inset-0 bg-black/10"></div>
    </div>

    <n-spin :show="loading" class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 -mt-24 relative z-10">
      <template #description>
        <span>{{ t('common.loading') }}</span>
      </template>

      <!-- Error state -->
      <div v-if="loadError" class="flex items-center justify-center min-h-[400px]">
        <n-result status="error" :title="t('profile.loadErrorTitle')" :description="loadErrorMsg">
          <template #footer>
            <n-button type="primary" round @click="loadAllData">{{ t('common.retry') }}</n-button>
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
            <n-tabs v-model:value="activeTab" type="line" animated size="large" pane-class="pt-6">
              <!-- 概览面板 -->
              <!-- T5 命名归一：个人概览 tab 不再借用仪表盘页名，改用本页域键 profile.overview -->
              <n-tab-pane name="overview" :tab="t('profile.overview')">
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
                        {{ t('profile.storage') }}
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
                              <span class="text-gray-500 dark:text-gray-400">{{ t('profile.totalProgress') }}</span>
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
                              <div class="text-xs text-blue-500 font-semibold mb-1">{{ t('profile.used') }}</div>
                              <div class="text-lg font-bold text-gray-800 dark:text-gray-200">{{ formatBytes(storageUsedBytes) }}</div>
                            </div>
                            <div class="p-3 bg-emerald-50/50 dark:bg-emerald-900/10 rounded-lg">
                              <div class="text-xs text-emerald-500 font-semibold mb-1">{{ t('profile.available') }}</div>
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
                        {{ t('profile.activityTrend') }}
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
                        {{ t('profile.recentActivity') }}
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
                      <div class="text-xs opacity-80 mb-2">{{ t('profile.systemActivityTitle') }}</div>
                      <div class="text-3xl font-bold">{{ workflowCompletion }}%</div>
                      <p class="text-xs mt-2 opacity-90 leading-relaxed">
                        {{ t('profile.systemActivityDesc') }}
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
                </div>
              </n-tab-pane>

              <!-- 偏好设置面板 -->
              <n-tab-pane name="preferences" :tab="t('profile.preferences')">
                 <div class="max-w-2xl space-y-6">
                    <div class="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800 rounded-xl">
                      <div>
                        <div class="font-medium text-gray-800 dark:text-white">{{ t('profile.theme') }}</div>
                        <div class="text-sm text-gray-500 mt-1">{{ t('profile.themeSettingDesc') }}</div>
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
                        <div class="text-sm text-gray-500 mt-1">{{ t('profile.languageSettingDesc') }}</div>
                      </div>
                      <n-select
                        v-model:value="settingsForm.language"
                        :options="languageOptions"
                        class="w-40"
                        @update:value="handleLanguageChange"
                      />
                    </div>

                    <!-- AI 模型设置（本批：真实生效——保存后 Chat 与 Agent 的 LLM
                         调用使用个人 provider/model/key；未配置回退服务端默认） -->
                    <div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-xl space-y-4 mt-4">
                      <div class="font-medium text-gray-800 dark:text-white">{{ t('profile.aiProviderTitle') }}</div>
                      <n-alert type="info" :bordered="false" class="text-xs">
                        {{ t('profile.aiProviderDesc') }}
                      </n-alert>
                      <n-alert v-if="providersLoadError" type="error" :bordered="false" class="text-xs">
                        {{ t('profile.providerLoadFailed') }}
                      </n-alert>
                      <n-alert
                        v-if="providerForm.hasSavedConfig && providerForm.source === 'server'"
                        type="warning"
                        :bordered="false"
                        class="text-xs"
                        data-testid="provider-fallback-hint"
                      >
                        {{ t('profile.aiProviderFallbackHint') }}
                      </n-alert>

                      <div class="flex items-center justify-between p-3 bg-white dark:bg-gray-900/40 rounded-lg">
                        <div>
                          <div class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ t('profile.aiProviderUseServerDefault') }}</div>
                          <div class="text-xs text-gray-400 mt-0.5">{{ t('profile.aiProviderUseServerDefaultDesc') }}</div>
                        </div>
                        <n-switch v-model:value="providerForm.useServerDefault" />
                      </div>

                      <div>
                        <div class="text-sm text-gray-500 mb-2">{{ t('profile.aiProviderProvider') }}</div>
                        <n-select v-model:value="providerForm.provider" :options="providerOptions" @update:value="onProviderChange" size="small" :disabled="providerForm.useServerDefault" />
                      </div>
                      <div>
                        <div class="text-sm text-gray-500 mb-2">{{ t('profile.aiProviderModel') }}</div>
                        <n-select v-model:value="providerForm.model" :options="currentModelOptions" :allow-input="providerForm.provider === 'custom'" filterable size="small" :disabled="providerForm.useServerDefault" />
                      </div>
                      <div v-if="currentProviderNeedsKey && !providerForm.useServerDefault">
                        <div class="text-sm text-gray-500 mb-2">API Key</div>
                        <n-input v-model:value="providerForm.api_key" type="password" show-password-on="click" placeholder="sk-..." size="small" />
                        <div class="text-xs text-gray-400 mt-1">{{ t('profile.aiProviderKeyHint') }}</div>
                      </div>
                      <div v-if="providerForm.provider === 'custom' && !providerForm.useServerDefault">
                        <div class="text-sm text-gray-500 mb-2">Base URL</div>
                        <n-input v-model:value="providerForm.base_url" placeholder="https://api.example.com/v1" size="small" />
                      </div>

                      <div class="flex flex-wrap items-center gap-3 pt-1">
                        <n-button type="primary" size="small" @click="handleSaveProviders" :loading="savingProviders">
                          {{ t('profile.aiProviderSave') }}
                        </n-button>
                        <n-text v-if="providerSaveMessage" :type="providerSaveSuccess ? 'success' : 'error'" class="text-sm">
                          {{ providerSaveMessage }}
                        </n-text>
                      </div>
                      <n-text depth="3" class="text-xs block">
                        {{ providerForm.source === 'user' ? t('profile.aiProviderSourceUser') : t('profile.aiProviderSourceServer') }}
                      </n-text>
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

              <n-tab-pane name="sessions" :tab="t('profile.tabs.sessions')">
                <n-data-table
                  :columns="sessionColumns"
                  :data="sessions"
                  :pagination="sessionPagination"
                  :row-key="(row: UserSession) => row.id"
                  :bordered="false"
                  size="small"
                />
              </n-tab-pane>

              <n-tab-pane name="audit" :tab="t('profile.tabs.audit')">
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
  loading, loadError, loadErrorMsg, activeTab, userInfo, stats, activities, sessions, auditLogs,
  sessionPagination, auditPagination, sessionColumns,
  showSessionDetail, auditColumns, storageUsage, storageUsedBytes, storageLimitBytes,
  storageMetricsSource, formatBytes, statItems, workflowCompletion, quickActions,
  goToRoute, themeOptions, languageOptions, handleLanguageChange, settingsForm,
  savingSettings, settingsSaveMessage, settingsSaveSuccess, providers, providerForm,
  providerOptions, currentProvider, currentModelOptions, currentProviderNeedsKey,
  providersLoadError, onProviderChange, loadProviders, settingsChanged, handleSaveSettings, updatingProfile,
  savingProviders, providerSaveMessage, providerSaveSuccess, handleSaveProviders,
  updatingPassword, storageChartRef, activityChartRef, initCharts, handleResize,
  editFormRef, passwordFormRef, editForm, passwordForm,
  editFormRules, passwordFormRules, getApiBaseUrl, normalizeAvatarUrl,
  handleAvatarUpload, loadUserProfile, loadUserStats, loadActivities, loadSessions,
  loadAuditLogs, formatAuditAction, handleRevokeSession, handleUpdateProfile,
  handleChangePassword, loadAllData, formatDate,
} = useProfilePage()
</script>

<style scoped>
/* 自定义滚动条等样式 */
</style>
