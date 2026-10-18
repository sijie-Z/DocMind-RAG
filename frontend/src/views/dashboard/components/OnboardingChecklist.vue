<template>
  <!-- B5 · 注册后 4 步清单：完成态由真实数据推导；跳过折叠为常驻入口，不消失 -->
  <section
    v-if="statsReady"
    data-testid="onboarding-checklist"
    class="bg-white dark:bg-gray-800 rounded-2xl border border-blue-100 dark:border-blue-900/40 shadow-sm overflow-hidden"
  >
    <!-- 折叠态（跳过之后）：保留进度与再次查看入口 -->
    <div
      v-if="collapsed"
      data-testid="onboarding-collapsed"
      class="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
    >
      <div class="flex items-center gap-3 min-w-0">
        <div class="w-9 h-9 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center flex-shrink-0">
          <n-icon size="20" class="text-blue-600 dark:text-blue-400"><RocketOutline /></n-icon>
        </div>
        <div class="min-w-0">
          <p class="font-semibold text-gray-900 dark:text-white truncate">{{ t('dashboard.onboarding.title') }}</p>
          <p class="text-xs text-gray-500 dark:text-gray-400 truncate">
            <span data-testid="onboarding-progress">{{ progressText }}</span>
            · {{ t('dashboard.onboarding.skippedHint') }}
          </p>
        </div>
      </div>
      <n-button size="small" type="primary" secondary data-testid="onboarding-expand" @click="expand">
        {{ t('dashboard.onboarding.show') }}
      </n-button>
    </div>

    <!-- 展开态：4 步清单 -->
    <div v-else data-testid="onboarding-steps">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-start justify-between gap-3 bg-gradient-to-r from-blue-50 to-transparent dark:from-blue-900/10">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center flex-shrink-0">
            <n-icon size="20" class="text-blue-600 dark:text-blue-400"><RocketOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.onboarding.title') }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.onboarding.subtitle') }}</p>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <span data-testid="onboarding-progress" class="text-sm font-medium text-blue-600 dark:text-blue-400">
            {{ progressText }}
          </span>
          <n-button text size="small" :loading="llmLoading" data-testid="onboarding-refresh" @click="refreshAll">
            {{ t('dashboard.onboarding.refresh') }}
          </n-button>
          <n-button text size="small" data-testid="onboarding-skip" @click="skip">
            {{ t('dashboard.onboarding.skip') }}
          </n-button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 p-4">
        <div
          v-for="(step, idx) in steps"
          :key="step.key"
          :data-testid="`onboarding-step-${step.key}`"
          :data-done="step.done ? 'true' : 'false'"
          class="rounded-xl border p-4 flex flex-col gap-2 transition-colors"
          :class="step.done
            ? 'border-emerald-200 dark:border-emerald-800/60 bg-emerald-50/60 dark:bg-emerald-900/10'
            : 'border-gray-100 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-700/30'"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-medium text-gray-400">
              {{ t('dashboard.onboarding.stepLabel', { index: idx + 1 }) }}
            </span>
            <n-icon :size="20" :class="step.done ? 'text-emerald-500' : 'text-gray-300 dark:text-gray-600'">
              <CheckmarkCircleOutline v-if="step.done" />
              <EllipseOutline v-else />
            </n-icon>
          </div>
          <p class="font-semibold text-sm text-gray-900 dark:text-white">{{ step.title }}</p>
          <p class="text-xs text-gray-500 dark:text-gray-400 flex-1">{{ step.desc }}</p>
          <n-button
            v-if="step.route && !step.done"
            size="tiny"
            secondary
            type="primary"
            :data-testid="`onboarding-action-${step.key}`"
            @click="$router.push(step.route)"
          >
            {{ step.action }}
          </n-button>
          <span v-else-if="step.key === 'model' && !step.done" class="text-xs text-amber-600 dark:text-amber-400">
            {{ modelStatusText }}
          </span>
          <span v-else-if="step.done" class="text-xs font-medium text-emerald-600 dark:text-emerald-400">
            {{ t('dashboard.onboarding.done') }}
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckmarkCircleOutline,
  EllipseOutline,
  RocketOutline,
} from '@vicons/ionicons5'
import { getLLMStatus } from '@/api/monitoring'

/**
 * B5 · 注册后 4 步清单（连模型 → 建知识库 → 传文档 → 首次问答）。
 *
 * 完成态口径（优先真实数据）：
 * - 连模型：GET /monitoring/llm-status 的 configured（服务端实际生效配置）；
 * - 建知识库：file_count > 0（知识库中已有文档条目）；
 * - 传文档：knowledge_count > 0（文档完成索引，可被检索）；
 * - 首次问答：message_count >= 2（至少一轮用户提问 + 助手回答）。
 *
 * 跳过/恢复：跳过只折叠清单，不删除；折叠态按用户写入 localStorage
 * （docmind.onboarding.skipped.<userId>），刷新/重登后恢复，换账号互不影响。
 */
const props = defineProps<{
  statsReady: boolean
  fileCount: number
  knowledgeCount: number
  messageCount: number
  userId?: number
}>()

const emit = defineEmits<{ refresh: [] }>()

const { t } = useI18n()

const llmConfigured = ref<boolean | null>(null)
const llmChecked = ref(false)
const llmError = ref(false)
const llmLoading = ref(false)
const collapsed = ref(false)

const skippedKey = computed(() => `docmind.onboarding.skipped.${props.userId ?? 'guest'}`)

function syncFromStorage() {
  try {
    collapsed.value = localStorage.getItem(skippedKey.value) === '1'
  } catch {
    collapsed.value = false
  }
}

function skip() {
  collapsed.value = true
  try {
    localStorage.setItem(skippedKey.value, '1')
  } catch {
    /* 隐私模式/存储不可用时仅本次会话折叠 */
  }
}

function expand() {
  collapsed.value = false
  try {
    localStorage.removeItem(skippedKey.value)
  } catch {
    /* 同上 */
  }
}

async function checkModel() {
  llmLoading.value = true
  llmError.value = false
  try {
    const res = await getLLMStatus()
    llmConfigured.value = res.data?.data?.configured === true
  } catch {
    // 网络/服务失败 ≠ 未配置：保持 null 并单独提示，避免误报「未检测到模型」
    llmConfigured.value = null
    llmError.value = true
  } finally {
    llmChecked.value = true
    llmLoading.value = false
  }
}

/** 模型步骤状态文案：检查中 / 检查失败 / 确认未配置，三种状态不混为一谈。 */
const modelStatusText = computed(() => {
  // 请求在途一律显示「检查中」：重检时不得先闪回上一次的「未配置」文案
  if (llmLoading.value || !llmChecked.value) return t('dashboard.onboarding.steps.model.checking')
  if (llmError.value) return t('dashboard.onboarding.steps.model.unknown')
  return t('dashboard.onboarding.steps.model.hint')
})

/** 重新检查：本地模型状态 + 让父级重拉统计/会话（步骤 2-4 的真实数据）。 */
function refreshAll() {
  emit('refresh')
  void checkModel()
}

onMounted(() => {
  syncFromStorage()
  void checkModel()
})

watch(skippedKey, syncFromStorage)

const steps = computed(() => [
  {
    key: 'model',
    done: llmConfigured.value === true,
    title: t('dashboard.onboarding.steps.model.title'),
    desc: t('dashboard.onboarding.steps.model.desc'),
    action: '',
    route: '',
  },
  {
    key: 'knowledge',
    done: props.fileCount > 0,
    title: t('dashboard.onboarding.steps.knowledge.title'),
    desc: t('dashboard.onboarding.steps.knowledge.desc'),
    action: t('dashboard.onboarding.steps.knowledge.action'),
    route: '/knowledge',
  },
  {
    key: 'upload',
    done: props.knowledgeCount > 0,
    title: t('dashboard.onboarding.steps.upload.title'),
    desc: t('dashboard.onboarding.steps.upload.desc'),
    action: t('dashboard.onboarding.steps.upload.action'),
    route: '/knowledge',
  },
  {
    key: 'chat',
    done: props.messageCount >= 2,
    title: t('dashboard.onboarding.steps.chat.title'),
    desc: t('dashboard.onboarding.steps.chat.desc'),
    action: t('dashboard.onboarding.steps.chat.action'),
    route: '/chat',
  },
])

const doneCount = computed(() => steps.value.filter((s) => s.done).length)
const progressText = computed(() =>
  t('dashboard.onboarding.progress', { done: doneCount.value, total: steps.value.length }),
)
</script>
