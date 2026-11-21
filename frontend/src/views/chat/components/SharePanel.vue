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

        <div v-else-if="shared" class="mb-4 rounded-lg border border-gray-200 p-3 text-sm dark:border-gray-700">
          {{ t('chat.share.activeNotice') }}
        </div>
        <div v-else class="mb-4 text-sm text-gray-500 dark:text-gray-400">{{ t('chat.share.inactive') }}</div>

        <div class="flex flex-wrap gap-2">
          <n-button data-testid="share-create" type="primary" :loading="busy" @click="createShare">
            {{ shared ? t('chat.share.regenerate') : t('chat.share.create') }}
          </n-button>
          <n-button data-testid="share-copy" :disabled="!token" @click="copyLink">{{ t('chat.share.copyLink') }}</n-button>
          <n-button data-testid="share-revoke" :disabled="!shared" :loading="busy" @click="revokeShare">{{ t('chat.share.revoke') }}</n-button>
        </div>
        <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400" role="alert">{{ error }}</p>
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
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const link = computed(() => `${window.location.origin}/share/${token.value}`)

const loadStatus = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await getConversationShareStatus(props.conversationId)
    shared.value = !!res.data?.data?.shared
    token.value = '' // token 只在新创建时可得，不假设服务端会回传
  } catch {
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
    const res = await createConversationShare(props.conversationId)
    token.value = res.data?.data?.token || ''
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
    emit('revoked')
    message.success(t('chat.share.revoked'))
  } catch {
    error.value = t('chat.share.revokeFailed')
  } finally {
    busy.value = false
  }
}
</script>
