<template>
  <!-- 运行计时 + 成本可视化（体验第四波）：运行中显示秒级计时；终态显示
       总用时 / tokens（execution_context 数据）/ 估算成本（价目表可配置，
       恒带"估算"标注——诚实口径，见 utils/cost.ts）。 -->
  <div class="run-stats" data-testid="run-stats">
    <span class="run-stats-duration">
      {{ loading ? t('runStats.running', { t: durationText }) : t('runStats.elapsed', { t: durationText }) }}
    </span>
    <template v-if="!loading">
      <span v-if="tokens && tokens > 0" class="run-stats-tokens" data-testid="run-stats-tokens">
        {{ tokens.toLocaleString('en-US') }} Tokens
      </span>
      <span v-if="costText" class="run-stats-cost" data-testid="run-stats-cost">
        {{ costText }}
      </span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { estimateCostUsd, formatCostUsd, formatDuration } from '@/utils/cost'

const props = defineProps<{
  /** 运行中：秒级计时器跳动；终态：冻结为总用时。 */
  loading: boolean
  /** 运行开始时间（毫秒时间戳；无则不渲染，由父级 v-if 控制）。 */
  startedAt?: number
  /** 终态时间（done/error/cancelled 落下）；缺省时按当前时刻冻结。 */
  endedAt?: number
  /** 总 tokens（execution_context.total_tokens）。 */
  tokens?: number
  /** 本次运行模型（价目表查询键）。 */
  model?: string
}>()

const { t } = useI18n()

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

function startTicker() {
  stopTicker()
  now.value = Date.now()
  timer = setInterval(() => { now.value = Date.now() }, 1000)
}

function stopTicker() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => { if (props.loading) startTicker() })
onUnmounted(stopTicker)
watch(() => props.loading, (v) => { if (v) startTicker(); else stopTicker() })

const elapsedMs = computed(() => {
  if (!props.startedAt) return 0
  const end = props.loading ? now.value : (props.endedAt ?? now.value)
  return Math.max(0, end - props.startedAt)
})

const durationText = computed(() => formatDuration(elapsedMs.value))

const costText = computed(() => {
  if (!props.tokens || props.tokens <= 0) return ''
  const usd = estimateCostUsd(props.model || 'default', props.tokens)
  if (usd <= 0) return ''
  return t('runStats.cost', { c: formatCostUsd(usd) })
})
</script>

<style scoped>
.run-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 0 2px 4px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.run-stats-duration {
  font-variant-numeric: tabular-nums;
}

.run-stats-tokens,
.run-stats-cost {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--bg-tertiary, #f3f4f6);
}

.dark .run-stats-tokens,
.dark .run-stats-cost {
  background: var(--bg-tertiary-dark, #374151);
}
</style>
