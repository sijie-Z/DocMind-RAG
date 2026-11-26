<template>
  <div
    v-if="show"
    class="fixed inset-0 z-40"
    data-testid="session-tree-panel"
    @keydown.esc.stop="close"
  >
    <div class="absolute inset-0 bg-black/30" @click="close"></div>
    <aside
      ref="dialogRef"
      class="absolute right-0 top-0 flex h-full w-[440px] max-w-full flex-col bg-white shadow-2xl dark:bg-gray-900"
      role="dialog"
      aria-modal="true"
      :aria-label="t('chat.sessionTree.title')"
      tabindex="-1"
      @keydown.tab="trapFocus"
    >
      <header
        class="flex items-start justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800"
      >
        <div>
          <h2 class="text-base font-semibold text-gray-900 dark:text-gray-100">
            {{ t('chat.sessionTree.title') }}
          </h2>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {{ t('chat.sessionTree.description') }}
          </p>
        </div>
        <button
          type="button"
          class="rounded-lg px-2 py-1 text-sm text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
          :aria-label="t('chat.sessionTree.close')"
          data-testid="session-tree-close"
          @click="close"
        >
          ✕
        </button>
      </header>

      <div class="flex-1 overflow-y-auto px-4 py-3">
        <div v-if="loading" class="py-8 text-center text-sm text-gray-500" data-testid="session-tree-loading">
          {{ t('chat.sessionTree.loading') }}
        </div>

        <div v-else-if="error" class="py-8 text-center" data-testid="session-tree-error">
          <p class="text-sm text-red-600 dark:text-red-400">{{ t('chat.sessionTree.loadFailed') }}</p>
          <button
            type="button"
            class="mt-3 rounded-lg border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
            data-testid="session-tree-retry"
            @click="loadTree()"
          >{{ t('chat.sessionTree.retry') }}</button>
        </div>

        <template v-else>
          <input
            v-model="labelFilter"
            type="search"
            class="mb-3 w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-slate-400 focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
            :placeholder="t('chat.sessionTree.labelPlaceholder')"
            :aria-label="t('chat.sessionTree.labelPlaceholder')"
            data-testid="session-tree-filter"
          />

          <p v-if="tree?.tree_truncated" class="mb-2 text-xs text-amber-700 dark:text-amber-400">
            {{ t('chat.sessionTree.treeTruncated') }}
          </p>

          <div
            v-if="!branches.length"
            class="py-8 text-center text-sm text-gray-500 dark:text-gray-400"
            data-testid="session-tree-empty"
          >{{ t('chat.sessionTree.empty') }}</div>

          <div
            v-else-if="!visibleBranches.length"
            class="py-8 text-center text-sm text-gray-500 dark:text-gray-400"
            data-testid="session-tree-filter-empty"
          >{{ t('chat.sessionTree.filterEmpty') }}</div>

          <ul v-else class="space-y-2" data-testid="session-tree-list">
            <li
              v-for="node in visibleBranches"
              :key="node.session_id"
              class="rounded-xl border p-3 transition-colors"
              :class="[
                node.is_current
                  ? 'border-slate-300 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/30'
                  : 'border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:hover:bg-gray-800/60',
                'session-tree-node',
                node.is_current ? 'session-tree-node--active' : ''
              ]"
              :style="{ marginLeft: `${Math.min(node.depth, 20) * 14}px` }"
              :data-session-id="node.session_id"
              :data-active="node.is_current ? 'true' : 'false'"
            >
              <button
                type="button"
                class="flex w-full items-start gap-2 text-left"
                data-testid="session-tree-node"
                :aria-current="node.is_current ? 'true' : undefined"
                :title="node.title || node.session_id"
                @click="selectBranch(node)"
              >
                <span
                  class="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full"
                  :class="node.is_current ? 'bg-slate-500' : 'bg-gray-300 dark:bg-gray-600'"
                ></span>
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {{ node.title || t('chat.defaultTitle') }}
                  </span>
                  <span class="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-gray-500 dark:text-gray-400">
                    <span
                      v-if="node.is_current"
                      class="rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                      data-testid="session-tree-current"
                    >{{ t('chat.sessionTree.current') }}</span>
                    <span
                      v-if="node.is_root"
                      class="rounded bg-gray-100 px-1.5 py-0.5 dark:bg-gray-800"
                    >{{ t('chat.sessionTree.root') }}</span>
                    <span data-testid="session-tree-message-count">
                      {{ t('chat.sessionTree.messageCount', { count: node.message_count }) }}
                    </span>
                    <span data-testid="session-tree-artifacts">
                      {{ node.artifact_count
                        ? t('chat.sessionTree.artifactStats', {
                            count: node.artifact_count,
                            version: node.artifact_latest_version
                          })
                        : t('chat.sessionTree.noArtifacts') }}
                    </span>
                  </span>
                </span>
              </button>

              <p
                v-if="node.branch_label"
                class="mt-1 truncate text-xs text-gray-600 dark:text-gray-300"
                data-testid="session-tree-label"
              >{{ node.branch_label }}</p>

              <p
                v-if="node.fork_point_preview"
                class="mt-1 line-clamp-2 text-xs text-gray-500 dark:text-gray-400"
                data-testid="session-tree-fork-point"
              >{{ t('chat.sessionTree.forkPoint') }}: {{ node.fork_point_preview }}</p>

              <p
                v-if="node.branch_summary"
                class="mt-1 line-clamp-3 text-xs text-gray-600 dark:text-gray-300"
                data-testid="session-tree-summary"
              >{{ node.branch_summary }}</p>
              <p
                v-else
                class="mt-1 text-xs text-gray-400 dark:text-gray-500"
                data-testid="session-tree-no-summary"
              >{{ t('chat.sessionTree.noSummary') }}</p>

              <p
                v-if="summaryErrors[node.session_id]"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
                role="alert"
                data-testid="session-tree-summary-error"
              >{{ summaryErrorText(summaryErrors[node.session_id]) }}</p>

              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="rounded-md border border-gray-200 px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
                  :disabled="summaryBusyId === node.session_id"
                  data-testid="session-tree-summarize"
                  @click.stop="generateSummary(node)"
                >{{ summaryBusyId === node.session_id
                  ? t('chat.sessionTree.summarizing')
                  : t('chat.sessionTree.generateSummary') }}</button>
                <button
                  type="button"
                  class="rounded-md border border-gray-200 px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
                  data-testid="session-tree-edit-label"
                  @click.stop="startEditLabel(node)"
                >{{ t('chat.sessionTree.editLabel') }}</button>
              </div>

              <div
                v-if="editingId === node.session_id"
                class="mt-2 flex items-center gap-2"
                data-testid="session-tree-label-editor"
              >
                <input
                  v-model="draftLabel"
                  type="text"
                  maxlength="100"
                  class="min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  :placeholder="t('chat.sessionTree.labelInput')"
                  :aria-label="t('chat.sessionTree.labelInput')"
                  data-testid="session-tree-label-input"
                  @keydown.enter.prevent="saveLabel(node)"
                  @keydown.esc.prevent.stop="editingId = ''"
                />
                <button
                  type="button"
                  class="rounded-md bg-slate-600 px-2 py-1 text-[11px] text-white hover:bg-slate-700"
                  data-testid="session-tree-label-save"
                  @click.stop="saveLabel(node)"
                >{{ t('chat.sessionTree.saveLabel') }}</button>
                <button
                  type="button"
                  class="rounded-md px-2 py-1 text-[11px] text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
                  data-testid="session-tree-label-cancel"
                  @click.stop="editingId = ''"
                >{{ t('chat.sessionTree.cancel') }}</button>
              </div>

              <p
                v-if="labelError && editingId === node.session_id"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
                role="alert"
                data-testid="session-tree-label-error"
              >{{ t('chat.sessionTree.labelFailed') }}</p>
            </li>
          </ul>
        </template>
      </div>

      <footer class="border-t border-gray-100 px-4 py-3 dark:border-gray-800">
        <button
          type="button"
          class="w-full rounded-lg bg-slate-600 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          :disabled="shareBusy || !shareRootId"
          data-testid="session-tree-share"
          @click="shareTree"
        >{{ t('chat.sessionTree.share') }}</button>
        <p
          v-if="shareCopied"
          class="mt-2 text-xs text-emerald-700 dark:text-emerald-400"
          role="status"
          data-testid="session-tree-share-copied"
        >{{ t('chat.sessionTree.shareCopied') }}</p>
        <p
          v-if="shareError"
          class="mt-2 text-xs text-red-600 dark:text-red-400"
          role="alert"
          data-testid="session-tree-share-error"
        >{{ shareError }}</p>
        <input
          v-if="shareLink"
          :value="shareLink"
          readonly
          class="mt-2 w-full rounded border border-gray-200 bg-gray-50 px-2 py-1 text-[11px] text-gray-600 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-300"
          data-testid="session-tree-share-link"
        />
        <p class="mt-2 text-[11px] text-gray-400 dark:text-gray-500">
          {{ t('chat.sessionTree.shareHint') }}
        </p>
      </footer>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { sessionsApi, type SessionTreeBranch, type SessionTreeData } from '@/api/sessions'

const props = defineProps<{ show: boolean; sessionId: string }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; select: [sessionId: string] }>()
const { t } = useI18n()

const tree = ref<SessionTreeData | null>(null)
const loading = ref(false)
const error = ref(false)
const labelFilter = ref('')

const summaryBusyId = ref('')
const summaryErrors = ref<Record<string, 'unavailable' | 'empty' | 'failed'>>({})
const editingId = ref('')
const draftLabel = ref('')
const labelError = ref(false)

const shareBusy = ref(false)
const shareLink = ref('')
const shareCopied = ref(false)
const shareError = ref('')

const branches = computed(() => tree.value?.branches ?? [])
const visibleBranches = computed(() => {
  const q = labelFilter.value.trim().toLowerCase()
  if (!q) return branches.value
  return branches.value.filter((node) => {
    const label = (node.branch_label || '').toLowerCase()
    const title = (node.title || '').toLowerCase()
    return label.includes(q) || title.includes(q)
  })
})
// 只在树成功加载且根仍可见时允许分享；绝不用当前分支做静默兜底，
// 否则树未加载/根已删除时会把子分支当根分享出去。
const shareRootId = computed(() =>
  tree.value?.root_present ? tree.value.root_session_id : '',
)

const dialogRef = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null

watch(
  () => props.show,
  async (visible) => {
    if (visible) {
      previousFocus =
        document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      dialogRef.value?.focus()
    } else {
      previousFocus?.focus()
      previousFocus = null
    }
  },
)

function trapFocus(event: KeyboardEvent) {
  const root = dialogRef.value
  if (!root) return
  const focusables = Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => el.offsetParent !== null)
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  }
}

const close = () => emit('update:show', false)

function summaryErrorText(code: 'unavailable' | 'empty' | 'failed'): string {
  if (code === 'unavailable') return t('chat.sessionTree.summaryFailed')
  if (code === 'empty') return t('chat.sessionTree.summaryEmpty')
  return t('chat.sessionTree.summaryFailedGeneric')
}

let treeRequestSeq = 0

async function loadTree(sessionId: string = props.sessionId) {
  const seq = ++treeRequestSeq
  tree.value = null
  error.value = false
  summaryErrors.value = {}
  labelFilter.value = ''
  shareLink.value = ''
  shareCopied.value = false
  shareError.value = ''
  if (!sessionId) return
  loading.value = true
  try {
    const res = await sessionsApi.tree(sessionId)
    if (seq !== treeRequestSeq) return
    const data = apiUnwrap<SessionTreeData | null>(res)
    if (data && Array.isArray(data.branches)) {
      tree.value = data
    } else {
      error.value = true
    }
  } catch {
    if (seq !== treeRequestSeq) return
    error.value = true
  } finally {
    if (seq === treeRequestSeq) loading.value = false
  }
}

function selectBranch(node: SessionTreeBranch) {
  if (node.is_current) {
    close()
    return
  }
  emit('select', node.session_id)
  close()
}

async function generateSummary(node: SessionTreeBranch) {
  if (summaryBusyId.value) return
  summaryBusyId.value = node.session_id
  const next = { ...summaryErrors.value }
  delete next[node.session_id]
  summaryErrors.value = next
  try {
    const res = await sessionsApi.summarize(node.session_id, 'llm')
    const data = apiUnwrap<{
      generated?: boolean
      summary?: string | null
      summary_model?: string | null
      summarized_at?: string | null
    } | null>(res)
    if (!data?.generated || !data.summary) {
      // 空分支：清掉旧摘要，避免树继续显示与本次响应矛盾的陈旧内容。
      node.branch_summary = null
      node.summary_model = null
      node.summarized_at = null
      summaryErrors.value = { ...summaryErrors.value, [node.session_id]: 'empty' }
      return
    }
    node.branch_summary = data.summary
    node.summary_model = data.summary_model ?? null
    node.summarized_at = data.summarized_at ?? null
  } catch (err) {
    const errorCode = (
      err as { response?: { data?: { error_code?: string } } }
    )?.response?.data?.error_code
    summaryErrors.value = {
      ...summaryErrors.value,
      [node.session_id]: errorCode === 'SUMMARY_MODEL_UNAVAILABLE' ? 'unavailable' : 'failed',
    }
  } finally {
    summaryBusyId.value = ''
  }
}

function startEditLabel(node: SessionTreeBranch) {
  editingId.value = node.session_id
  draftLabel.value = node.branch_label || ''
  labelError.value = false
}

async function saveLabel(node: SessionTreeBranch) {
  const nextLabel = draftLabel.value.trim() || null
  try {
    const res = await sessionsApi.setBranchLabel(node.session_id, nextLabel)
    const data = apiUnwrap<{ branch_label?: string | null } | null>(res)
    node.branch_label = data?.branch_label ?? nextLabel
    editingId.value = ''
    labelError.value = false
  } catch {
    labelError.value = true
  }
}

async function copyLink(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

async function shareTree() {
  if (shareBusy.value || !shareRootId.value) return
  shareBusy.value = true
  shareError.value = ''
  shareCopied.value = false
  try {
    const res = await sessionsApi.shareTree(shareRootId.value)
    const data = apiUnwrap<{ url?: string } | null>(res)
    if (!data?.url) throw new Error('missing share url')
    shareLink.value = `${window.location.origin}${data.url}`
  } catch {
    shareError.value = t('chat.sessionTree.shareFailed')
    shareBusy.value = false
    return
  }
  try {
    // 链接已创建成功；复制失败不应显示成「分享失败」。
    await copyLink(shareLink.value)
    shareCopied.value = true
  } catch {
    shareCopied.value = false
  } finally {
    shareBusy.value = false
  }
}

watch(
  () => [props.show, props.sessionId] as const,
  ([show, sessionId]) => {
    if (show) void loadTree(sessionId)
  },
  { immediate: true },
)
</script>
