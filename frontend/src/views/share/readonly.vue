<template>
  <div class="share-page min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
    <header class="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
      <div class="max-w-3xl mx-auto px-4 py-4 flex items-center gap-3">
        <div class="inline-flex items-center justify-center bg-blue-700 p-1.5 rounded-lg text-white">
          <DocMindMark :size="18" />
        </div>
        <div class="min-w-0">
          <h1 class="text-base font-semibold truncate">{{ title || t('chat.share.title') }}</h1>
          <p class="text-xs text-gray-500 dark:text-gray-400">{{ t('chat.share.readOnlyNotice') }}</p>
        </div>
      </div>
    </header>

    <main class="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <div v-if="loading" class="text-sm text-gray-500 dark:text-gray-400" role="status">
        {{ t('chat.share.loading') }}
      </div>
      <div v-else-if="error" class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300" role="alert">
        {{ t('chat.share.unavailable') }}
      </div>
      <template v-else>
        <article
          v-for="msg in messages"
          :key="msg.id || msg.content"
          class="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
        >
          <div class="mb-2 text-xs font-medium text-gray-500 dark:text-gray-400">
            {{ msg.messageType === 'user' ? t('chat.roleUser') : t('chat.roleAssistant') }}
          </div>
          <Markdown :content="msg.content" />
          <div v-if="msg.sources?.length" class="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
            <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('chat.share.sources') }}</div>
            <ul class="flex flex-wrap gap-2">
              <li v-for="(source, i) in msg.sources" :key="i" class="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300">
                {{ source.filename || t('chat.sourceDoc') }}
              </li>
            </ul>
          </div>
        </article>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Markdown from '@/components/common/Markdown.vue'
import DocMindMark from '@/components/brand/DocMindMark.vue'
import { getSharedConversation, type SharedMessage } from '@/api/share'

const { t } = useI18n()
const route = useRoute()
const messages = ref<SharedMessage[]>([])
const title = ref('')
const loading = ref(true)
const error = ref(false)

onMounted(async () => {
  const token = String(route.params.token || '')
  try {
    const res = await getSharedConversation(token)
    const data = res.data?.data
    if (!res.data?.success || !data) throw new Error('unavailable')
    title.value = data.title || ''
    messages.value = data.messages || []
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
})
</script>
