<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-5xl mx-auto">
      <div class="mb-6">
        <h1 class="text-3xl font-bold text-gray-800 dark:text-white">{{ t('menu.organizationSettings') }}</h1>
        <p class="text-gray-500 dark:text-gray-400 mt-1 text-sm">{{ t('orgSettings.subtitle') }}</p>
      </div>

      <n-alert v-if="!orgId" type="warning" :show-icon="true">
        {{ t('orgSettings.noOrganization') }}
      </n-alert>

      <template v-else>
        <n-alert v-if="loadError" type="error" class="mb-4" :show-icon="true">
          <div class="flex items-center justify-between gap-4">
            <span>{{ loadError }}</span>
            <n-button size="small" @click="load">{{ t('common.retry') }}</n-button>
          </div>
        </n-alert>

        <n-spin :show="loading">
          <n-card :title="t('orgSettings.title')" class="mb-6" :bordered="false">
            <n-form label-placement="left" label-width="200" :show-feedback="false">
              <n-form-item :label="t('orgSettings.fields.defaultProvider')">
                <n-select
                  v-model:value="form.defaultProvider"
                  :options="providerOptions"
                  :placeholder="t('orgSettings.providerPlaceholder')"
                  clearable
                />
              </n-form-item>
              <n-form-item :label="t('orgSettings.fields.defaultModel')">
                <n-input
                  v-model:value="form.defaultModel"
                  :placeholder="t('orgSettings.modelPlaceholder')"
                  clearable
                  maxlength="100"
                />
              </n-form-item>
              <n-form-item :label="t('orgSettings.fields.retrievalTopK')">
                <n-input-number
                  v-model:value="form.retrievalTopK"
                  :min="1"
                  :max="50"
                  clearable
                  class="w-40"
                />
              </n-form-item>
              <n-form-item :label="t('orgSettings.fields.allowPrivacyMode')">
                <n-switch v-model:value="form.allowPrivacyMode" />
                <span class="ml-3 text-xs text-gray-400">{{ t('orgSettings.allowPrivacyModeHint') }}</span>
              </n-form-item>
              <n-form-item :label="t('orgSettings.fields.monthlyTokenQuota')">
                <n-input-number
                  v-model:value="form.monthlyTokenQuota"
                  :min="0"
                  clearable
                  class="w-48"
                  :placeholder="t('orgSettings.unlimited')"
                />
              </n-form-item>
              <n-form-item :label="t('orgSettings.fields.toolWhitelist')">
                <n-dynamic-tags v-model:value="form.toolWhitelist" />
              </n-form-item>
            </n-form>
            <p class="text-xs text-gray-400 mt-2">{{ t('orgSettings.toolWhitelistHint') }}</p>
            <p class="text-xs text-amber-500 dark:text-amber-400 mt-1">{{ t('orgSettings.runtimeHint') }}</p>
            <template #action>
              <div class="flex gap-2 justify-end">
                <n-button quaternary :disabled="saving" @click="load">{{ t('orgSettings.reload') }}</n-button>
                <n-button type="primary" :loading="saving" @click="save">{{ t('orgSettings.save') }}</n-button>
              </div>
            </template>
          </n-card>

          <n-card :title="t('orgSettings.effectiveTitle')" class="mb-6" :bordered="false">
            <n-table :bordered="false" :single-line="false">
              <thead>
                <tr>
                  <th>{{ t('orgSettings.columns.setting') }}</th>
                  <th>{{ t('orgSettings.columns.value') }}</th>
                  <th>{{ t('orgSettings.columns.source') }}</th>
                  <th>{{ t('orgSettings.columns.restart') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in settingRows" :key="row.key" :data-testid="`effective-${row.key}`">
                  <td>{{ row.label }}</td>
                  <td class="break-all">{{ formatValue(row.key, row.entry) }}</td>
                  <td>
                    <n-tag size="small" :type="row.entry?.source === 'organization' ? 'success' : 'default'" :bordered="false">
                      {{ row.entry?.source === 'organization' ? t('orgSettings.sourceOrganization') : t('orgSettings.sourceDefault') }}
                    </n-tag>
                  </td>
                  <td>
                    <n-tag size="small" :type="row.entry?.requires_restart ? 'warning' : 'default'" :bordered="false">
                      {{ row.entry?.requires_restart ? t('orgSettings.restartRequired') : t('orgSettings.restartNotRequired') }}
                    </n-tag>
                  </td>
                </tr>
              </tbody>
            </n-table>
            <p class="text-xs text-gray-500 mt-3">
              {{ t('orgSettings.usage', {
                used: usage.month_tokens,
                quota: usage.monthly_token_quota === null ? t('orgSettings.unlimited') : usage.monthly_token_quota,
              }) }}
            </p>
          </n-card>

          <n-card :title="t('orgSettings.auditTitle')" :bordered="false">
            <n-spin v-if="auditLoading" size="small" class="mb-3" data-testid="audit-loading" />
            <n-alert
              v-if="auditError"
              type="warning"
              class="mb-3"
              :show-icon="true"
              data-testid="audit-error"
            >
              {{ auditError }}
            </n-alert>
            <n-empty
              v-if="!auditLoading && !auditError && auditItems.length === 0"
              :description="t('orgSettings.auditEmpty')"
            />
            <n-table v-if="auditItems.length > 0" :bordered="false" :single-line="false">
              <thead>
                <tr>
                  <th>{{ t('orgSettings.auditColumns.time') }}</th>
                  <th>{{ t('orgSettings.auditColumns.user') }}</th>
                  <th>{{ t('orgSettings.auditColumns.detail') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in auditItems" :key="item.id">
                  <td class="whitespace-nowrap">{{ item.created_at || '' }}</td>
                  <td>{{ item.username || item.user_id }}</td>
                  <td class="text-xs break-all">{{ item.detail || '' }}</td>
                </tr>
              </tbody>
            </n-table>
          </n-card>
        </n-spin>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { useDedupedMessage } from '@/utils/message'
import { getErrorMessage } from '@/utils/apiError'
import {
  getOrganizationSettings,
  getOrganizationSettingsAudit,
  updateOrganizationSettings,
} from '@/api/organization'
import type {
  EffectiveOrganizationSetting,
  OrganizationSettingsAuditItem,
  OrganizationSettingsUpdatePayload,
  OrganizationSettingsUsage,
} from '@/api/organization'

const { t } = useI18n()
const message = useDedupedMessage()
const userStore = useUserStore()

const orgId = computed(() => userStore.currentOrgId)

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const auditError = ref('')
const auditLoading = ref(false)
const settings = ref<Record<string, EffectiveOrganizationSetting>>({})
const usage = ref<OrganizationSettingsUsage>({ month_tokens: 0, monthly_token_quota: null })
const auditItems = ref<OrganizationSettingsAuditItem[]>([])

const form = reactive<{
  defaultProvider: string | null
  defaultModel: string | null
  retrievalTopK: number | null
  allowPrivacyMode: boolean
  monthlyTokenQuota: number | null
  toolWhitelist: string[]
}>({
  defaultProvider: null,
  defaultModel: null,
  retrievalTopK: null,
  allowPrivacyMode: true,
  monthlyTokenQuota: null,
  toolWhitelist: [],
})

// 表单初值快照：保存时只发送真正变化的字段，避免「读一次默认值就写出一条
// 组织覆盖」——来源列会因此从 default 漂移成 organization。
const baseline = reactive({ ...form })

const providerOptions = [
  { label: 'DeepSeek', value: 'deepseek' },
  { label: 'OpenAI', value: 'openai' },
  { label: 'Qwen', value: 'qwen' },
  { label: 'Zhipu', value: 'zhipu' },
  { label: 'StepFun', value: 'step' },
  { label: 'Local', value: 'local' },
]

const settingRows = computed(() => [
  { key: 'default_provider', label: t('orgSettings.settingNames.defaultProvider'), entry: settings.value.default_provider },
  { key: 'default_model', label: t('orgSettings.settingNames.defaultModel'), entry: settings.value.default_model },
  { key: 'retrieval_top_k', label: t('orgSettings.settingNames.retrievalTopK'), entry: settings.value.retrieval_top_k },
  { key: 'allow_privacy_mode', label: t('orgSettings.settingNames.allowPrivacyMode'), entry: settings.value.allow_privacy_mode },
  { key: 'monthly_token_quota', label: t('orgSettings.settingNames.monthlyTokenQuota'), entry: settings.value.monthly_token_quota },
  { key: 'tool_whitelist', label: t('orgSettings.settingNames.toolWhitelist'), entry: settings.value.tool_whitelist },
])

const formatValue = (key: string, entry?: EffectiveOrganizationSetting): string => {
  if (!entry) return t('orgSettings.notSet')
  const value = entry.value
  if (value === null || value === undefined) {
    return key === 'monthly_token_quota' ? t('orgSettings.unlimited') : t('orgSettings.notSet')
  }
  if (typeof value === 'boolean') {
    return value ? t('orgSettings.enabled') : t('orgSettings.disabled')
  }
  if (Array.isArray(value)) {
    // 空数组 = 显式「不允许任何工具」，与未设置（null）必须区分展示。
    return value.length ? value.join(', ') : '[]'
  }
  return String(value)
}

const applySettings = (data: {
  settings: Record<string, EffectiveOrganizationSetting>
  usage: OrganizationSettingsUsage
}) => {
  settings.value = data.settings
  usage.value = data.usage
  form.defaultProvider = (data.settings.default_provider?.value as string | null) ?? null
  form.defaultModel = (data.settings.default_model?.value as string | null) ?? null
  form.retrievalTopK = (data.settings.retrieval_top_k?.value as number | null) ?? null
  form.allowPrivacyMode = data.settings.allow_privacy_mode?.value !== false
  form.monthlyTokenQuota = (data.settings.monthly_token_quota?.value as number | null) ?? null
  form.toolWhitelist = (data.settings.tool_whitelist?.value as string[] | null) ?? []
  Object.assign(baseline, {
    defaultProvider: form.defaultProvider,
    defaultModel: form.defaultModel,
    retrievalTopK: form.retrievalTopK,
    allowPrivacyMode: form.allowPrivacyMode,
    monthlyTokenQuota: form.monthlyTokenQuota,
    toolWhitelist: [...form.toolWhitelist],
  })
}

const buildPayload = (): OrganizationSettingsUpdatePayload => {
  const payload: OrganizationSettingsUpdatePayload = {}
  if (form.defaultProvider !== baseline.defaultProvider) {
    payload.default_provider = form.defaultProvider
  }
  if (form.defaultModel !== baseline.defaultModel) {
    payload.default_model = form.defaultModel
  }
  if (form.retrievalTopK !== baseline.retrievalTopK) {
    payload.retrieval_top_k = form.retrievalTopK
  }
  if (form.allowPrivacyMode !== baseline.allowPrivacyMode) {
    payload.allow_privacy_mode = form.allowPrivacyMode
  }
  if (form.monthlyTokenQuota !== baseline.monthlyTokenQuota) {
    payload.monthly_token_quota = form.monthlyTokenQuota
  }
  const whitelist = form.toolWhitelist.length ? [...form.toolWhitelist] : null
  const baselineWhitelist = baseline.toolWhitelist.length ? [...baseline.toolWhitelist] : null
  if (JSON.stringify(whitelist) !== JSON.stringify(baselineWhitelist)) {
    payload.tool_whitelist = whitelist
  }
  return payload
}

/**
 * 审计是独立区块：拉取失败只降级审计区（保留旧列表 + 内联提示），
 * 不得影响设置的加载/保存结论。请求带 X-Silent-Error，由本分支唯一呈现错误。
 * 序号令牌保证只有最新一次刷新可提交结果（并发的加载/保存刷新、组织切换）。
 */
let auditRequestSeq = 0
const refreshAudit = async (id: number) => {
  const seq = ++auditRequestSeq
  auditLoading.value = true
  try {
    const auditRes = await getOrganizationSettingsAudit(id)
    if (orgId.value !== id || seq !== auditRequestSeq) return
    auditItems.value = auditRes.data?.items || []
    auditError.value = ''
  } catch (error) {
    if (orgId.value !== id || seq !== auditRequestSeq) return
    auditError.value = getErrorMessage(error, t('orgSettings.auditLoadFailed'))
  } finally {
    if (seq === auditRequestSeq) auditLoading.value = false
  }
}

const load = async () => {
  const id = orgId.value
  if (!id) return
  loading.value = true
  loadError.value = ''
  auditError.value = ''
  // 审计与设置并行拉取；审计失败不会让整页进入错误态。
  void refreshAudit(id)
  try {
    const settingsRes = await getOrganizationSettings(id, { silent: true })
    applySettings(settingsRes.data)
  } catch (error) {
    loadError.value = getErrorMessage(error, t('orgSettings.loadFailed'))
  } finally {
    loading.value = false
  }
}

const save = async () => {
  const id = orgId.value
  if (!id) return
  const payload = buildPayload()
  if (Object.keys(payload).length === 0) {
    message.info(t('orgSettings.noChanges'))
    return
  }
  saving.value = true
  try {
    const res = await updateOrganizationSettings(id, payload)
    applySettings(res.data)
    message.success(t('orgSettings.saveSuccess'))
    // 审计刷新是保存后的独立副作用：失败不回写「保存失败」，
    // 由审计区内联降级提示呈现。
    void refreshAudit(id)
  } catch (error) {
    message.error(getErrorMessage(error, t('orgSettings.saveFailed')))
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})

// 组织切换后重新拉取（B1 会用页面 key 重挂载兜底；此 watcher 保证不重挂时
// 也不会把旧组织的表单值保存进新组织）。
watch(orgId, () => {
  // 切换组织先清空审计列表：不得把旧组织的审计记录展示在新组织下。
  auditItems.value = []
  void load()
})
</script>
