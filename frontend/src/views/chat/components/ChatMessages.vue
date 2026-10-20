<template>
  <div
    ref="chatContainer"
    class="flex-1 overflow-y-auto px-4 pt-20 pb-40 scroll-smooth relative"
    @scroll="$emit('scroll')"
  >
    <div class="max-w-3xl mx-auto space-y-6">
      <!-- Welcome screen（E7：历史加载中不显示空态欢迎屏，避免「加载中」与「新会话」混淆） -->
      <div v-if="messages.length === 0 && !isLoadingHistory" class="flex flex-col items-center justify-center mt-16 md:mt-24 text-center animate-fade-in">
        <div class="relative mb-10">
          <div class="w-28 h-28 bg-gradient-to-br from-slate-500 via-blue-500 to-blue-600 rounded-3xl shadow-2xl shadow-slate-500/30 flex items-center justify-center transform hover:scale-105 transition-transform duration-300">
            <n-icon size="52" class="text-white drop-shadow">
              <SparklesOutline />
            </n-icon>
          </div>
          <div class="absolute -inset-2 bg-slate-400/20 rounded-[1.5rem] blur-xl -z-10"></div>
        </div>
        <h2 class="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
          {{ t('chat.welcome') }}
        </h2>
        <p class="text-base md:text-lg text-slate-500 dark:text-slate-400 max-w-md mb-10 font-light leading-relaxed">
          {{ t('chat.welcomeDesc') }}
        </p>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
          <button
            v-for="(suggestion, index) in suggestions"
            :key="index"
            class="p-4 md:p-5 bg-white/80 dark:bg-gray-800/80 backdrop-blur border border-slate-200/60 dark:border-gray-700/60 rounded-2xl text-left hover:border-slate-400/60 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group relative overflow-hidden"
            @click="$emit('useSuggestion', suggestion)"
          >
            <div class="absolute top-0 right-0 p-3 opacity-0 group-hover:opacity-100 transition-all duration-300 transform translate-x-2 group-hover:translate-x-0">
              <n-icon class="text-slate-500" size="18"><ArrowForwardOutline /></n-icon>
            </div>
            <h3 class="font-semibold text-sm md:text-base text-slate-800 dark:text-slate-200 group-hover:text-slate-600 dark:group-hover:text-slate-400 mb-1.5 tracking-wide">
              {{ suggestion.title }}
            </h3>
            <p class="text-xs md:text-sm text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed font-light">
              {{ suggestion.desc }}
            </p>
          </button>
        </div>
      </div>

      <!-- E7：历史加载态 —— 与生成态严格分离（独立指示，不出现停止按钮/生成
           loading/流式光标；loadConversation 不再复用 isLoading） -->
      <div v-if="isLoadingHistory" data-testid="history-loading" class="flex items-center gap-3 py-2 text-sm text-gray-500 dark:text-gray-400 animate-pulse">
        <div class="w-10 h-10 bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 flex items-center justify-center shadow-sm">
          <n-icon size="20" class="text-primary-600 dark:text-primary-400 animate-spin-slow"><SparklesOutline /></n-icon>
        </div>
        <span>{{ t('chat.loadingHistory') }}</span>
      </div>

      <!-- Message list -->
      <DynamicScroller
        :items="messages"
        :min-item-size="120"
        key-field="id"
        class="flex-1"
        v-slot="{ item: message, index, active }"
      >
        <DynamicScrollerItem
          :item="message"
          :active="active"
          :index="index"
          :data-index="index"
        >
          <div
            class="flex gap-4 animate-slide-in-bottom group mb-4"
            :class="{ 'flex-row-reverse': message.messageType === 'user' }"
          >
            <div
              class="flex-shrink-0 w-10 h-10 rounded-2xl flex items-center justify-center overflow-hidden shadow-lg mt-1 transform transition-all duration-300 group-hover:scale-110 group-hover:shadow-blue-500/20"
              :class="message.messageType === 'user' ? 'bg-gradient-to-br from-gray-800 to-black' : 'bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700'"
            >
              <n-avatar
                v-if="message.messageType === 'user'"
                :src="userAvatar"
                :fallback-src="'/default-avatar.png'"
                size="small"
                class="bg-transparent"
              />
              <n-icon v-else size="20" class="text-slate-600 dark:text-slate-400"><SparklesOutline /></n-icon>
            </div>

            <div class="max-w-[85%] relative group/bubble">
              <div
                class="px-6 py-4 rounded-[1.5rem] shadow-sm text-[15px] leading-relaxed tracking-wide transition-all duration-300"
                :class="message.messageType === 'user'
                  ? 'bg-gradient-to-br from-slate-600 to-blue-600 text-white rounded-tr-md'
                  : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-tl-md border border-gray-100 dark:border-gray-700'"
              >
                <!-- P1 双渲染修复（UI 验收 round2 待办#1）：原结构中光标 span 的
                     v-if 劫持了 v-else 的配对——loading 结束后 v-else=true，
                     assistant 消息的 Markdown 与纯文本副本同时渲染。
                     重排为「assistant → Markdown(+光标) | 否则 → 纯文本」严格互斥。 -->
                <template v-if="message.messageType === 'assistant'">
                  <!-- E7：thinking 折叠区（SSE thinking 事件 reasoning/planning/correction
                       已由 useChatSend 落账到 message.thinking，此前聊天页零渲染）——
                       默认收起，点击展开，避免推理过程长期占屏 -->
                  <div v-if="message.thinking" class="mb-2" data-testid="thinking-block">
                    <button
                      type="button"
                      data-testid="thinking-toggle"
                      class="thinking-toggle inline-flex items-center gap-1 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                      :aria-expanded="isThinkingOpen(message, index)"
                      @click="toggleThinking(message, index)"
                    >
                      <n-icon size="12">
                        <ChevronDownOutline v-if="isThinkingOpen(message, index)" />
                        <ChevronForwardOutline v-else />
                      </n-icon>
                      {{ t('chat.thinkingLabel') }}
                    </button>
                    <div
                      v-if="isThinkingOpen(message, index)"
                      data-testid="thinking-content"
                      class="mt-1.5 whitespace-pre-wrap rounded-lg border border-gray-100 dark:border-gray-700/60 bg-gray-50 dark:bg-gray-900/40 px-3 py-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400"
                    >{{ message.thinking }}</div>
                  </div>
                  <!-- E3：最后一条流式消息标记 streaming —— 中间态轻量渲染，终态一次完整 Markdown -->
                  <Markdown :content="message.content" :streaming="isLoading && index === messages.length - 1" />
                  <span
                    v-if="isLoading && index === messages.length - 1"
                    class="inline-block w-0.5 h-4 bg-slate-500 dark:bg-slate-400 ml-0.5 align-middle animate-blink"
                  />
                  <!-- E7：已停止态 —— 停止生成后给该条消息打标，与「完成」明确区分 -->
                  <div v-if="message.stopped" data-testid="stopped-notice" class="mt-2 flex flex-wrap items-center gap-2 text-xs">
                    <span class="inline-flex items-center gap-1 rounded-full border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 font-medium text-amber-700 dark:text-amber-400">
                      <n-icon size="12"><StopCircleOutline /></n-icon>
                      {{ t('chat.generationStopped') }}
                    </span>
                    <span class="text-gray-400 dark:text-gray-500">{{ t('chat.stoppedHint') }}</span>
                  </div>
                </template>
                <div v-else class="whitespace-pre-wrap">{{ message.content }}</div>

                <!-- Agent 模式（useAgent）PER 事件（issue #289）：run_report 折叠块
                     + retry_hint 提示行 + 计划树 + reflection，与 Agent 页复用同一
                     组组件/口径，保证同一 run 双入口信息同构 -->
                <template v-if="message.messageType === 'assistant'">
                  <RunReportBlock :report="message.runReport" :total-tokens="totalTokensOf(message)" />
                  <RetryHintLine
                    v-for="(evt, eIdx) in agentEventsOf(message, 'retry_hint')"
                    :key="'rh-' + eIdx"
                    :content="evt.content"
                  />
                  <PlanTree
                    v-if="message.planSteps && message.planSteps.length"
                    :steps="message.planSteps"
                    :progress="message.planProgress"
                    class="mt-3"
                  />
                  <ReflectionBanner
                    v-for="(evt, eIdx) in agentEventsOf(message, 'reflection')"
                    :key="'rf-' + eIdx"
                    :result="evt.reflection_result || 'pass'"
                    :text="evt.content"
                  />
                </template>

                <div v-if="message.sources && message.sources.length > 0" class="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                  <div class="flex items-center gap-1.5 mb-1.5">
                    <span class="text-xs text-gray-500 dark:text-gray-400 font-medium">{{ t('chat.sourcesLabel') }}</span>
                    <!-- T4（第二轮 §7.2④）：示例对话来源是写死的假分数（demo 标记），
                         角标如实标注「演示数据」；分数数字保留（体验价值），只标注不隐藏 -->
                    <span
                      v-if="hasDemoSources(message)"
                      data-testid="demo-source-badge"
                      class="inline-flex items-center rounded-full border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-900/20 px-1.5 py-[1px] text-[10px] font-medium leading-4 text-amber-700 dark:text-amber-400"
                    >{{ t('chat.demoData') }}</span>
                  </div>
                  <div class="flex flex-wrap gap-1.5">
                    <n-popover
                      v-for="(source, idx) in message.sources"
                      :key="idx"
                      trigger="hover"
                      placement="top"
                      :delay="200"
                      :duration="200"
                    >
                      <template #trigger>
                        <n-tag
                          size="tiny"
                          round
                          :bordered="false"
                          class="cursor-pointer bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
                          @click="$emit('openSource', source)"
                        >
                          <template #icon>
                            <n-icon size="10" class="mr-0.5"><DocumentTextOutline /></n-icon>
                          </template>
                          [{{ Number(idx) + 1 }}] {{ source.filename || source.title || t('chat.sourceDoc') }}
                        </n-tag>
                      </template>
                      <div class="max-w-sm p-2">
                        <div class="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                          {{ source.filename || t('chat.unknownDoc') }}
                        </div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-6">
                          {{ source.content || t('chat.noPreview') }}
                        </div>
                        <!-- issue #185：诚实分数口径——有重排绝对分才显示「重排分」，
                             否则只报排序序位；不再把 min-max 相对分当「相关度 %」 -->
                        <div class="mt-1.5 text-[10px] text-gray-400">
                          {{ sourceScoreText(source, idx) }}
                        </div>
                      </div>
                    </n-popover>
                  </div>
                </div>
              </div>

              <!-- T11：键盘/触屏可达的操作排——hover 显隐只在支持 hover 的设备上生效，
                   键盘 :focus-visible 与无 hover 设备常显（样式见 scoped CSS） -->
              <div v-if="message.messageType === 'assistant'" class="msg-actions flex items-center gap-1 mt-2" data-testid="message-actions">
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle
                      :class="message.feedback === 1 ? 'text-green-600 bg-green-50 dark:bg-green-900/30' : 'text-gray-400 hover:text-green-600'"
                      @click="$emit('feedback', message, 1)"
                    >
                      <template #icon><n-icon><ThumbsUpOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.useful') }}
                </n-tooltip>
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle
                      :class="message.feedback === -1 ? 'text-red-600 bg-red-50 dark:bg-red-900/30' : 'text-gray-400 hover:text-red-600'"
                      @click="$emit('feedback', message, -1)"
                    >
                      <template #icon><n-icon><ThumbsDownOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.notUseful') }}
                </n-tooltip>
                <n-button size="tiny" quaternary circle class="text-gray-400" @click="$emit('copy', message.content)">
                  <template #icon><n-icon><CopyOutline /></n-icon></template>
                </n-button>
                <n-tooltip v-if="index === messages.length - 1 && message.messageType === 'assistant'" trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-blue-600"
                      @click="$emit('regenerate', message)">
                      <template #icon><n-icon><RefreshOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.regenerate') }}
                </n-tooltip>
              </div>
            </div>
          </div>
        </DynamicScrollerItem>
      </DynamicScroller>

      <!-- Loading indicator（E7：生成态专用，历史加载走上方 history-loading） -->
      <div v-if="isLoading" data-testid="generation-loading" class="flex gap-4 animate-pulse">
        <div class="w-10 h-10 bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 flex items-center justify-center shadow-sm">
          <n-icon size="20" class="text-primary-600 dark:text-primary-400 animate-spin-slow"><SparklesOutline /></n-icon>
        </div>
        <div class="flex items-center h-10">
          <div class="flex space-x-1.5">
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 0ms"></div>
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 150ms"></div>
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 300ms"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Back to bottom button -->
    <transition name="fade">
      <div v-if="showBackToBottom" class="absolute bottom-32 left-1/2 -translate-x-1/2 z-20">
        <n-button
          round
          size="small"
          class="shadow-xl bg-white/80 dark:bg-gray-800/80 backdrop-blur-md border border-gray-200 dark:border-gray-700 text-primary-600 dark:text-primary-400 font-bold"
          @click="$emit('scrollToBottom', 'smooth', true)"
        >
          <template #icon><n-icon><ArrowDownOutline /></n-icon></template>
          {{ t('chat.backToBottom') }}
        </n-button>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SparklesOutline, ArrowForwardOutline, ArrowDownOutline, ThumbsUpOutline, ThumbsDownOutline, CopyOutline, DocumentTextOutline, RefreshOutline, ChevronDownOutline, ChevronForwardOutline, StopCircleOutline } from '@vicons/ionicons5'
import { NPopover } from 'naive-ui'
// 首屏瘦身（E5）：虚拟滚动只在聊天页使用——从全局注册（main.ts）改为本组件局部引入，
// 模块与样式随 Chat 路由 chunk 按需加载，不再计入首屏。
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import Markdown from '@/components/common/Markdown.vue'
import PlanTree from '@/components/agent/PlanTree.vue'
import ReflectionBanner from '@/components/agent/ReflectionBanner.vue'
import RetryHintLine from '@/components/agent/RetryHintLine.vue'
import RunReportBlock from '@/components/agent/RunReportBlock.vue'
import type { ChatMessage, KnowledgeSource } from '@/types/chat'
import type { AgentEvent } from '@/types/agent'

const { t } = useI18n()

defineProps<{
  messages: ChatMessage[]
  isLoading: boolean
  /** E7：历史加载态（与生成态 isLoading 分离，二者互斥出现） */
  isLoadingHistory?: boolean
  showBackToBottom: boolean
  userAvatar?: string
  suggestions: { title: string; desc: string }[]
}>()

// E7：thinking 折叠区展开状态（按消息 id 记忆；无 id 时回退索引）
const expandedThinking = ref<Record<string, boolean>>({})

function thinkingKey(message: ChatMessage, index: number): string {
  return String(message.id ?? index)
}

function isThinkingOpen(message: ChatMessage, index: number): boolean {
  return expandedThinking.value[thinkingKey(message, index)] === true
}

function toggleThinking(message: ChatMessage, index: number): void {
  const key = thinkingKey(message, index)
  expandedThinking.value = { ...expandedThinking.value, [key]: !expandedThinking.value[key] }
}

// issue #289：Agent 模式事件（落账见 useChatSend）的取数与渲染辅助
function agentEventsOf(message: ChatMessage, type: AgentEvent['type']): AgentEvent[] {
  return (message.events || []).filter((e) => e.type === type)
}

function totalTokensOf(message: ChatMessage): number | undefined {
  const value = message.executionContext?.total_tokens
  return typeof value === 'number' ? value : undefined
}

// T4（第二轮 §7.2④）：来源含 demo 标记（示例对话写死的假分数）→ 渲染「演示数据」角标；
// 真实检索结果不带该键、不得误标
function hasDemoSources(message: ChatMessage): boolean {
  return (message.sources || []).some((s) => s.demo === true)
}

// issue #185：分数展示契约——
// - relevanceScore 是重排器绝对分（仅新契约数据带 rank 标记时展示为「重排分」）；
// - 拿不到绝对分时只报排序序位（rank / 列表位置），不以 rankScore×100 冒充相关度。
function sourceScoreText(source: KnowledgeSource, index: number | string): string {
  if (typeof source.relevanceScore === 'number' && source.rank != null) {
    return t('chat.rerankScore', { score: source.relevanceScore.toFixed(2) })
  }
  return t('chat.rankPosition', { rank: source.rank ?? Number(index) + 1 })
}

defineEmits<{
  scroll: []
  scrollToBottom: [behavior: ScrollBehavior, force?: boolean]
  useSuggestion: [suggestion: { title: string; desc: string }]
  feedback: [msg: ChatMessage, feedback: number]
  copy: [text: string]
  regenerate: [msg: ChatMessage]
  openSource: [source: KnowledgeSource]
}>()
</script>

<style scoped>
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.animate-blink {
  animation: blink 0.8s step-end infinite;
}

/* T11 消息操作排显隐（键盘 / 触屏可达，鼠标行为不回退）：
   - 默认隐藏，保持界面干净；
   - 键盘聚焦 :has(:focus-visible) 常显（媒体查询之外，任何设备都生效）；
     用 :focus-visible 而非 :focus-within——鼠标点击按钮也会聚焦，用后者会
     在鼠标移开后把操作排滞留（旧 bug）；
   - hover 显隐只在支持 hover 的设备上生效，触屏不依赖它；
   - 无 hover 能力的设备（触屏）常显，保证可点可达。 */
.msg-actions {
  opacity: 0;
  transition: opacity 0.2s ease;
}
.msg-actions:has(:focus-visible) {
  opacity: 1;
}
@media (hover: hover) {
  .group\/bubble:hover .msg-actions {
    opacity: 1;
  }
}
@media (hover: none) {
  .msg-actions {
    opacity: 1;
  }
}
</style>
