<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('orgSkills.saveTitle')"
    class="max-w-lg"
    :mask-closable="!busy"
    data-testid="agent-save-skill-modal"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <div class="flex flex-col gap-3">
      <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
      <div>
        <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
          {{ t('orgSkills.saveName') }}
        </div>
        <n-input
          v-model:value="name"
          maxlength="100"
          :placeholder="t('orgSkills.saveNamePlaceholder')"
          data-testid="save-skill-name"
        />
      </div>
      <div>
        <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
          {{ t('orgSkills.saveDescription') }}
        </div>
        <n-input
          v-model:value="description"
          type="textarea"
          maxlength="300"
          :autosize="{ minRows: 2, maxRows: 4 }"
          :placeholder="t('orgSkills.saveDescriptionPlaceholder')"
          data-testid="save-skill-description"
        />
      </div>
      <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('orgSkills.saveHint') }}</p>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <n-button :disabled="busy" @click="emit('update:show', false)">
          {{ t('orgSkills.cancel') }}
        </n-button>
        <n-button
          type="primary"
          :loading="busy"
          data-testid="save-skill-submit"
          @click="submit"
        >
          {{ t('orgSkills.saveSubmit') }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { orgSkillsApi } from '@/api/orgSkills'
import { getErrorMessage } from '@/utils/apiError'
import { useDedupedMessage } from '@/utils/message'

const props = defineProps<{
  show: boolean
  taskId: string
  defaultName: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: []
}>()

const { t } = useI18n()
const message = useDedupedMessage()

const name = ref('')
const description = ref('')
const busy = ref(false)
const error = ref('')

// 打开时按任务默认名初始化；关闭不清空（再次打开会重新初始化）。
watch(
  () => [props.show, props.taskId] as const,
  ([show]) => {
    if (!show) return
    name.value = props.defaultName
    description.value = ''
    error.value = ''
  },
  { immediate: true },
)

async function submit() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    error.value = t('orgSkills.saveNameRequired')
    return
  }
  if (!props.taskId || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await orgSkillsApi.propose({
      name: trimmed,
      description: description.value.trim() || null,
      source_task_id: props.taskId,
    })
    message.success(t('orgSkills.saveSuccess'))
    emit('update:show', false)
    emit('saved')
  } catch (err) {
    error.value = getErrorMessage(err) || t('orgSkills.saveFailed')
  } finally {
    busy.value = false
  }
}
</script>
