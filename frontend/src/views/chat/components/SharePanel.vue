<template>
  <n-modal :show="show" @update:show="$emit('update:show', $event)">
    <n-card class="share-panel max-w-lg" :title="t('chat.share.title')" role="dialog" :aria-label="t('chat.share.title')">
      <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">{{ t('chat.share.description') }}</p>

      <div v-if="loading" class="text-sm text-gray-500" role="status">{{ t('chat.share.loading') }}</div>
      <template v-else>
        <div v-if="token" class="mb-4 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
          <label class="block text-xs font-medium mb-1" for="share-link">{{ t('chat.share.linkLabel') }}</label>
          <input id="share-link" data-testid="share-link" class="w-full rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900" :value="link" readonly />
          <label class="mt-3 block text-xs font-medium mb-1" for="share-token">{{ t('chat.share.tokenLabel') }}</label>
          <input id="share-token" data-testid="share-token" class="w-full rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900" :value="token" readonly />
          <p class="mt-2 text-xs text-amber-700 dark:text-amber-400">{{ t('chat.share.onceNotice') }}</p>
        </div>

        <div v-else-if="expired" data-testid="share-expired" class="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300">
          {{ t('chat.share.expiredNotice') }}
        </div>
        <div v-else-if="shared" class="mb-4 rounded-lg border border-gray-200 p-3 text-sm dark:border-gray-700">
          {{ t('chat.share.activeNotice') }}
        </div>
        <div v-else class="mb-4 text-sm text-gray-500 dark:text-gray-400">{{ t('chat.share.inactive') }}</div>

        <div class="mb-3 flex items-center gap-2">
          <label class="text-xs font-medium" for="share-expiry">{{ t('chat.share.expiryLabel') }}</label>
          <select
            id="share-expiry"
            v-model.number="expiryChoice"
            data-testid="share-expiry"
            class="rounded border border-gray-200 bg-white px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900"
          >
            <option :value="0">{{ t('chat.share.expiryNever') }}</option>
            <option :value="7">{{ t('chat.share.expiryDays', { days: 7 }) }}</option>
            <option :value="30">{{ t('chat.share.expiryDays', { days: 30 }) }}</option>
            <option :value="90">{{ t('chat.share.expiryDays', { days: 90 }) }}</option>
          </select>
        </div>
        <p v-if="expiresAt" data-testid="share-expires-at" class="mb-3 text-xs text-gray-500 dark:text-gray-400">
          {{ t('chat.share.expiresAt', { time: formatTime(expiresAt) }) }}
        </p>

        <div class="flex flex-wrap gap-2">
          <n-button data-testid="share-create" type="primary" :loading="busy" @click="createShare">
            {{ shared ? t('chat.share.regenerate') : t('chat.share.create') }}
          </n-button>
          <n-button data-testid="share-copy" :disabled="!token" @click="copyLink">{{ t('chat.share.copyLink') }}</n-button>
          <n-button data-testid="share-revoke" :disabled="!shared" :loading="busy" @click="revokeShare">{{ t('chat.share.revoke') }}</n-button>
        </div>
        <p v-if="error" data-testid="share-error" class="mt-3 text-sm text-red-600 dark:text-red-400" role="alert">{{ error }}</p>
      </template>
    </n-card>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { createConversationShare, getConversationShareStatus, revokeConversationShare } from '@/api/share'
import { useDedupedMessage } from '@/utils/message'
import { useConfirmDangerousAction } from '@/utils/confirm'

const props = defineProps<{ show: boolean; conversationId: string }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; revoked: [] }>()
const { t } = useI18n()
const message = useDedupedMessage()
const confirmDangerousAction = useConfirmDangerousAction()

const shared = ref(false)
const token = ref('')
const expiresAt = ref<string | null>(null)
const serverExpired = ref(false)
const expiryChoice = ref(7)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const link = computed(() => `${window.location.origin}/share/${token.value}`)

/** 已过期提示以服务端时钟为准；本地时钟只作回退（状态缺失 expired 时）。 */
const expired = computed(() => {
  if (!shared.value) return false
  if (serverExpired.value) return true
  if (!expiresAt.value) return false
  const time = new Date(expiresAt.value).getTime()
  return !Number.isNaN(time) && time <= Date.now()
})

function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** 从现有过期时间回填档位。
 * 已过期 → 7 天（重新生成时给出安全默认，而不是静默变回永久链接）；
 * 无过期（旧永久链接）→ 0（保持用户既有语义）。 */
function expiryChoiceFrom(value: string | null): number {
  if (!value) return 0
  const remaining = new Date(value).getTime() - Date.now()
  if (Number.isNaN(remaining)) return 0
  if (remaining <= 0) return 7
  const days = Math.ceil(remaining / 86_400_000)
  if (days <= 7) return 7
  if (days <= 30) return 30
  return 90
}

const loadStatus = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await getConversationShareStatus(props.conversationId)
    const data = res.data?.data
    shared.value = !!data?.shared
    expiresAt.value = data?.expiresAt ?? null
    serverExpired.value = !!data?.expired
    // 新建默认 7 天；已有链接回填当前档位，避免重新生成时静默改期。
    expiryChoice.value = shared.value ? expiryChoiceFrom(expiresAt.value) : 7
    token.value = '' // token 只在新创建时可得，不假设服务端会回传
  } catch {
    shared.value = false
    expiresAt.value = null
    serverExpired.value = false
    token.value = ''
    error.value = t('chat.share.loadFailed')
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (show) => { if (show) void loadStatus() }, { immediate: true })

const createShare = async () => {
  busy.value = true
  error.value = ''
  try {
    const res = await createConversationShare(
      props.conversationId,
      expiryChoice.value > 0 ? expiryChoice.value : null,
    )
    const data = res.data?.data
    token.value = data?.token || ''
    expiresAt.value = data?.expiresAt ?? null
    serverExpired.value = false
    shared.value = true
    message.success(t('chat.share.created'))
  } catch {
    error.value = t('chat.share.createFailed')
  } finally {
    busy.value = false
  }
}

const copyLink = async () => {
  if (!token.value) return
  try {
    await navigator.clipboard.writeText(link.value)
    message.success(t('chat.share.copied'))
  } catch {
    error.value = t('chat.share.copyFailed')
  }
}

const revokeShare = async () => {
  if (!shared.value) return
  const confirmed = await confirmDangerousAction(t('chat.share.revoke'), t('chat.share.revokeConfirm'))
  if (!confirmed) return
  busy.value = true
  error.value = ''
  try {
    await revokeConversationShare(props.conversationId)
    shared.value = false
    token.value = ''
    expiresAt.value = null
    serverExpired.value = false
    expiryChoice.value = 7
    emit('revoked')
    message.success(t('chat.share.revoked'))
  } catch {
    error.value = t('chat.share.revokeFailed')
  } finally {
    busy.value = false
  }
}
</script>
