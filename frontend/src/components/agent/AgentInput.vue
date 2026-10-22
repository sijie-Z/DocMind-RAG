<template>
  <div class="agent-input-area">
    <!-- 斜杠命令菜单（#475）：本地模板（search/doc/workflow）+ 服务端命令
         （GET /api/v1/commands，按当前会话 agent 作用域）。模板展开为提示词；
         服务端命令 emit command → 视图走 POST /commands/execute，不进入聊天流。 -->
    <div v-if="cmdOpen" class="cmd-menu" data-testid="cmd-menu">
      <div class="cmd-menu-title">{{ t('slashCmd.heading') }}</div>
      <div v-if="commandsLoading" class="cmd-state" data-testid="cmd-loading">
        {{ t('common.loading') }}
      </div>
      <div v-else-if="commandsError" class="cmd-state cmd-state-error" data-testid="cmd-error">
        {{ t('slashCmd.loadError') }}
      </div>
      <div v-else-if="!commandsEnabled" class="cmd-state" data-testid="cmd-unavailable">
        {{ t('slashCmd.unavailable') }}
      </div>
      <button
        v-for="(cmd, i) in cmdFiltered"
        :key="cmd.key"
        class="cmd-option"
        :class="{ active: i === cmdActive }"
        :data-cmd-kind="cmd.kind"
        :data-cmd-name="cmd.name"
        data-testid="cmd-option"
        @mousedown.prevent="applyCommand(cmd)"
      >
        <span class="cmd-name">/{{ cmd.name }}</span>
        <span class="cmd-desc">{{ cmd.description }}</span>
        <span v-if="cmd.kind === 'command'" class="cmd-kind">{{ t('slashCmd.commandBadge') }}</span>
      </button>
      <div v-if="cmdFiltered.length === 0" class="cmd-state" data-testid="cmd-empty">
        {{ t('slashCmd.empty') }}
      </div>
    </div>

    <!-- @引用选择层（体验第四波）：输入 @ 触发；搜索/多选/回车或点击选中。
         转向模式（steering）下不开放——转向通道不承载引用（诚实边界）。 -->
    <div v-if="pickerOpen" class="ref-picker" data-testid="ref-picker">
      <div class="ref-picker-title">{{ t('refPicker.heading') }}</div>
      <div v-if="pickerLoading" class="ref-picker-state">{{ t('common.loading') }}</div>
      <div v-else-if="pickerError" class="ref-picker-state ref-picker-error" data-testid="ref-picker-error">
        {{ t('refPicker.error') }}
      </div>
      <div v-else-if="pickerResults.length === 0" class="ref-picker-state" data-testid="ref-picker-empty">
        {{ t('refPicker.empty') }}
      </div>
      <button
        v-for="(doc, i) in pickerResults"
        :key="doc.id"
        class="ref-option"
        :class="{ active: i === pickerActive }"
        data-testid="ref-option"
        @mousedown.prevent="selectDoc(doc)"
      >
        <span class="ref-option-icon">📄</span>
        <span class="ref-option-title">{{ doc.title || doc.file_name || doc.id }}</span>
      </button>
    </div>

    <!-- 已选引用 chips（发送时随 referenced_document_ids 上送） -->
    <div v-if="selectedDocs.length > 0" class="ref-chips" data-testid="ref-chips">
      <span
        v-for="doc in selectedDocs"
        :key="doc.id"
        class="ref-chip"
        data-testid="ref-chip"
      >
        📄 {{ doc.title }}
        <button
          class="ref-chip-remove"
          :title="t('refPicker.remove')"
          data-testid="ref-chip-remove"
          @click="removeDoc(doc.id)"
        >×</button>
      </span>
    </div>

    <div class="input-row">
      <textarea
        v-model="inputText"
        @keydown.enter.exact.prevent="handleEnter"
        @keydown.shift.enter="inputText += '\n'"
        @keydown.down="onArrow(1, $event)"
        @keydown.up="onArrow(-1, $event)"
        @keydown.esc="onEsc"
        :placeholder="placeholder"
        :disabled="disabled"
        class="agent-textarea"
        rows="1"
        ref="textareaEl"
      ></textarea>
      <template v-if="steering">
        <!-- A：运行中转向（steering）——不打断当前步骤，步边界生效。
             转向模式出现时（running / waiting_approval）以"发送指导"为主动作；
             仍在运行中（loading）时保留"停止"。 -->
        <button
          class="steer-btn"
          :disabled="!inputText.trim() || disabled"
          data-testid="steer-btn"
          @click="handleSteer"
        >
          {{ t('hitl.steerSend') }}
        </button>
        <button
          v-if="loading"
          class="stop-btn"
          :disabled="stopping"
          @click="$emit('stop')"
        >
          {{ stopping ? '⏳ 停止中…' : '⏹ 停止' }}
        </button>
      </template>
      <button
        v-else-if="!loading"
        class="send-btn"
        :disabled="!inputText.trim() || disabled"
        @click="handleSend"
      >
        发送
      </button>
      <button
        v-else
        class="stop-btn"
        :disabled="stopping"
        @click="$emit('stop')"
      >
        {{ stopping ? '⏳ 停止中…' : '⏹ 停止' }}
      </button>
    </div>
    <div class="input-hint">
      {{ hintOverride || (steering ? t('hitl.steerHint') : t('refPicker.inputHint')) }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getKnowledgeBases, type KnowledgeBaseListResponse } from '@/api/knowledge'
import { commandsApi, type CommandInfo, type CommandListData } from '@/api/commands'
import type { ReferencedDoc } from '@/types/agent'
import { apiUnwrap } from '@/utils/apiUnwrap'

const props = defineProps<{
  placeholder?: string
  disabled?: boolean
  loading?: boolean
  /** HITL：服务端取消已受理、等待 run 在检查点收敛（按钮显示"停止中…"）。 */
  stopping?: boolean
  /** A：运行中转向——loading 时显示"发送指导"按钮，Enter 亦发送指导。 */
  steering?: boolean
  /** 覆盖提示行（如 B：等待计划审批时禁用输入并说明原因；空串回退默认提示）。 */
  hintOverride?: string
  /** issue #475：当前会话 id——GET /commands 据此合并 agent 作用域命令。 */
  sessionId?: string | null
}>()

const emit = defineEmits<{
  send: [text: string, refs?: ReferencedDoc[]]
  stop: []
  steer: [text: string]
  /** issue #475：选中服务端命令（完整命令行，含参数）；由视图调用 /commands/execute。 */
  command: [line: string]
}>()

const { t, te, locale } = useI18n()
const inputText = ref('')
const textareaEl = ref<HTMLTextAreaElement>()

// ── @引用（体验第四波）──────────────────────────────────────────────────────
// 触发规则（MVP，诚实边界）：只认"尾部 mention"——最后一段不含空白的 @token；
// token 后敲空格/换行即关闭。选择后该 @token 从输入框移除、转为 chip。
const selectedDocs = ref<ReferencedDoc[]>([])
const pickerOpen = ref(false)
const pickerQuery = ref('')
const pickerResults = ref<Array<{ id: string; title?: string; file_name?: string }>>([])
const pickerLoading = ref(false)
const pickerError = ref(false)
const pickerActive = ref(0)
let searchTimer: ReturnType<typeof setTimeout> | null = null

function closePicker() {
  pickerOpen.value = false
  pickerQuery.value = ''
  pickerResults.value = []
  pickerError.value = false
  pickerLoading.value = false
  pickerActive.value = 0
}

function updatePicker() {
  // 斜杠命令优先：以 / 开头时 @选择层让位（命令参数不做引用——诚实 MVP 边界）
  if (inputText.value.startsWith('/')) {
    closePicker()
    return
  }
  // 转向通道不承载引用：运行中的指导输入不开放 @选择层（诚实边界）
  if (props.steering || props.disabled) {
    closePicker()
    return
  }
  const text = inputText.value
  const at = text.lastIndexOf('@')
  if (at === -1) {
    closePicker()
    return
  }
  // @ 前须是行首/空白（邮箱等 a@b 不误触）
  if (at > 0 && !/[\s(（]/.test(text[at - 1])) {
    closePicker()
    return
  }
  const token = text.slice(at + 1)
  if (/\s/.test(token)) {
    closePicker()
    return
  }
  pickerOpen.value = true
  pickerQuery.value = token
  scheduleSearch()
}

function scheduleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  pickerLoading.value = true
  pickerError.value = false
  searchTimer = setTimeout(() => {
    searchTimer = null
    void doSearch()
  }, 250)
}

async function doSearch() {
  const q = pickerQuery.value
  try {
    const res = await getKnowledgeBases({ page: 1, page_size: 8, search: q || undefined })
    // 结果回来时现场已变（关层/换词）→ 丢弃
    if (!pickerOpen.value || pickerQuery.value !== q) return
    // 真实双层信封 → apiUnwrap 归一为分页体（data 为文件数组）
    const page = apiUnwrap<KnowledgeBaseListResponse>(res)
    const list = page?.data
    pickerResults.value = Array.isArray(list) ? list : []
    pickerActive.value = 0
    pickerError.value = false
  } catch {
    if (!pickerOpen.value || pickerQuery.value !== q) return
    pickerResults.value = []
    pickerError.value = true
  } finally {
    if (pickerOpen.value && pickerQuery.value === q) pickerLoading.value = false
  }
}

function selectDoc(doc: { id: string; title?: string; file_name?: string }) {
  if (!selectedDocs.value.some((d) => d.id === doc.id)) {
    selectedDocs.value.push({ id: doc.id, title: doc.title || doc.file_name || doc.id })
  }
  // 移除输入框中的尾部 @token 片段
  inputText.value = inputText.value.replace(/@[^\s]*$/, '')
  closePicker()
  nextTick(() => textareaEl.value?.focus())
}

function removeDoc(id: string) {
  selectedDocs.value = selectedDocs.value.filter((d) => d.id !== id)
}

// ── 斜杠命令（#475：模板 + 服务端命令）──────────────────────────────────────
// 触发规则：输入以 "/" 开头。"/cmd 参数" 形态下命令词照常匹配、参数保留。
// 菜单 = 本地模板（search/doc/workflow，展开为提示词）+ GET /commands 返回的
// 服务端命令（help/status/export/tools 等，选中后 emit command 交给视图执行）。
const TEMPLATE_IDS = ['search', 'doc', 'workflow'] as const

interface SlashMenuItem {
  key: string
  kind: 'template' | 'command'
  name: string
  description: string
  command?: CommandInfo
}

const cmdOpen = ref(false)
const cmdQuery = ref('')
const cmdArg = ref('')
const cmdActive = ref(0)

const serverCommands = ref<CommandInfo[]>([])
const commandsEnabled = ref(true)
const commandsLoading = ref(false)
const commandsError = ref(false)

/** 服务端命令描述优先用四语词条，其次后端 description_i18n，最后原文（避免回显裸键）。 */
function commandDescription(cmd: CommandInfo): string {
  const key = `slashCmd.cmdDesc.${cmd.name}`
  // 词条存在才取用（t() 的模板串形态被 tUsage 扫描器识别为可护前缀）
  if (te(key)) return t(`slashCmd.cmdDesc.${cmd.name}`)
  const localized = cmd.description_i18n?.[locale.value]
  if (localized) return localized
  return cmd.description_i18n?.en || cmd.description
}

const cmdFiltered = computed<SlashMenuItem[]>(() => {
  const templates: SlashMenuItem[] = TEMPLATE_IDS.map((id) => ({
    key: `tpl:${id}`,
    kind: 'template',
    name: id,
    description: t(`slashCmd.desc.${id}`),
  }))
  const commands: SlashMenuItem[] = serverCommands.value.map((cmd) => ({
    key: `cmd:${cmd.name}`,
    kind: 'command',
    name: cmd.name,
    description: commandDescription(cmd),
    command: cmd,
  }))
  const all = [...templates, ...commands]
  const q = cmdQuery.value.toLowerCase()
  if (!q) return all
  return all.filter((item) => {
    if (item.name.toLowerCase().startsWith(q)) return true
    if (item.kind === 'command') {
      return (item.command?.aliases || []).some((alias) =>
        alias.toLowerCase().startsWith(q),
      )
    }
    return false
  })
})

async function loadCommands() {
  commandsLoading.value = true
  commandsError.value = false
  try {
    const res = await commandsApi.list(props.sessionId || undefined)
    const data = apiUnwrap<CommandListData>(res)
    commandsEnabled.value = data?.enabled !== false
    serverCommands.value = Array.isArray(data?.commands) ? data!.commands : []
  } catch {
    // 命令通道不可用不是输入区的致命错误：模板仍可用，菜单里如实提示。
    commandsError.value = true
    serverCommands.value = []
  } finally {
    commandsLoading.value = false
  }
}

onMounted(() => {
  void loadCommands()
})

watch(
  () => props.sessionId,
  () => {
    void loadCommands()
  },
)

function closeCmd() {
  cmdOpen.value = false
  cmdQuery.value = ''
  cmdArg.value = ''
  cmdActive.value = 0
}

function updateCmd() {
  if (props.disabled || props.steering) {
    closeCmd()
    return
  }
  const text = inputText.value
  if (!text.startsWith('/')) {
    closeCmd()
    return
  }
  const rest = text.slice(1)
  const spaceIdx = rest.indexOf(' ')
  cmdQuery.value = spaceIdx >= 0 ? rest.slice(0, spaceIdx) : rest
  cmdArg.value = spaceIdx >= 0 ? rest.slice(spaceIdx + 1).trim() : ''
  cmdOpen.value = true
  cmdActive.value = 0
}

function applyCommand(item: SlashMenuItem) {
  if (item.kind === 'template') {
    inputText.value = t(`slashCmd.tpl.${item.name}`, { q: cmdArg.value })
    closeCmd()
    nextTick(() => textareaEl.value?.focus())
    return
  }
  // 服务端命令：完整命令行（命令名 + 已输入参数）交给视图执行；
  // 不进入聊天流（POST /commands/execute），也不把命令文本当作提示词。
  const arg = cmdArg.value.trim()
  const line = `/${item.name}` + (arg ? ` ${arg}` : '')
  emit('command', line)
  inputText.value = ''
  closeCmd()
  nextTick(() => textareaEl.value?.focus())
}

function onArrow(delta: number, e: KeyboardEvent) {
  if (cmdOpen.value && cmdFiltered.value.length > 0) {
    e.preventDefault()
    const n = cmdFiltered.value.length
    cmdActive.value = (cmdActive.value + delta + n) % n
    return
  }
  if (!pickerOpen.value || pickerResults.value.length === 0) return
  e.preventDefault()
  const n = pickerResults.value.length
  pickerActive.value = (pickerActive.value + delta + n) % n
}

function onEsc() {
  if (cmdOpen.value) closeCmd()
  if (pickerOpen.value) closePicker()
}

// ─────────────────────────────────────────────────────────────────────────────

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  // 有引用才带第二参（无引用保持既有单参契约，调用方与旧测试零改动）
  const refs = selectedDocs.value.length > 0 ? [...selectedDocs.value] : undefined
  if (refs) emit('send', text, refs)
  else emit('send', text)
  inputText.value = ''
  selectedDocs.value = []
  closePicker()
}

/** Enter：@选择层优先 → 斜杠命令次之（有匹配才拦截）→ 转向/发送。 */
function handleEnter() {
  if (pickerOpen.value && pickerResults.value.length > 0) {
    selectDoc(pickerResults.value[pickerActive.value])
    return
  }
  if (cmdOpen.value && cmdFiltered.value.length > 0) {
    const item = cmdFiltered.value[cmdActive.value] || cmdFiltered.value[0]
    if (item) applyCommand(item)
    return
  }
  if (props.steering) {
    handleSteer()
    return
  }
  handleSend()
}

function handleSteer() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('steer', text)
  inputText.value = ''
}

// Auto-resize textarea
watch(inputText, async () => {
  updateCmd()
  updatePicker()
  await nextTick()
  if (textareaEl.value) {
    textareaEl.value.style.height = 'auto'
    textareaEl.value.style.height = Math.min(textareaEl.value.scrollHeight, 120) + 'px'
  }
})

// 运行开始（转入 steering）时收起选择层与命令菜单
watch(() => props.steering, (v) => {
  if (v) {
    closePicker()
    closeCmd()
  }
})

onUnmounted(() => { if (searchTimer) clearTimeout(searchTimer) })

defineExpose({ clear: () => { inputText.value = ''; selectedDocs.value = []; closePicker(); closeCmd() } })
</script>

<style scoped>
.agent-input-area {
  position: relative;
  border-top: 1px solid var(--border-color, #e5e7eb);
  padding: 12px 16px;
}

.dark .agent-input-area {
  border-color: var(--border-color-dark, #374151);
}

.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.agent-textarea {
  flex: 1;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 13px;
  font-family: inherit;
  resize: none;
  outline: none;
  max-height: 120px;
  line-height: 1.5;
}

.dark .agent-textarea {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.agent-textarea:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
}

.agent-textarea:disabled {
  opacity: 0.6;
}

.send-btn, .stop-btn, .steer-btn {
  padding: 10px 20px;
  border-radius: 12px;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.send-btn {
  background: #3b82f6;
  color: white;
}

.send-btn:hover:not(:disabled) { background: #2563eb; }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* A：运行中转向——蓝色系（与"停止"的红形成动作区分），深色下同色仍达标 */
.steer-btn {
  background: #6366f1;
  color: white;
}

.steer-btn:hover:not(:disabled) { background: #4f46e5; }
.steer-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.dark .steer-btn { background: #818cf8; }
.dark .steer-btn:hover:not(:disabled) { background: #6366f1; }

.stop-btn {
  background: #ef4444;
  color: white;
}

.stop-btn:hover { background: #dc2626; }

.input-hint {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  text-align: center;
}

/* ── @引用选择层 / chips（体验第四波）────────────────────────────────────── */

.ref-picker,
.cmd-menu {
  position: absolute;
  bottom: 100%;
  left: 16px;
  right: 16px;
  margin-bottom: 6px;
  background: var(--bg-primary, #fff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 12px;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.08);
  max-height: 260px;
  overflow-y: auto;
  padding: 6px;
  z-index: 30;
}

.dark .ref-picker,
.dark .cmd-menu {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.4);
}

.ref-picker-title,
.cmd-menu-title {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-tertiary, #9ca3af);
  padding: 4px 8px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.ref-picker-state,
.cmd-state {
  font-size: 12px;
  color: var(--text-tertiary, #9ca3af);
  padding: 8px;
}

.ref-picker-error {
  color: #dc2626;
}

.ref-option,
.cmd-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
  padding: 7px 8px;
  border: none;
  border-radius: 8px;
  background: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-primary, #111827);
}

.dark .ref-option,
.dark .cmd-option {
  color: var(--text-primary-dark, #f9fafb);
}

.ref-option.active,
.ref-option:hover,
.cmd-option.active,
.cmd-option:hover {
  background: rgba(59, 130, 246, 0.1);
}

.ref-option-icon { flex-shrink: 0; }

.ref-option-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 斜杠命令：命令名（代码字体）+ 描述（弱化色） */
.cmd-name {
  flex-shrink: 0;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: #3b82f6;
}

.cmd-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary, #6b7280);
}

.cmd-kind {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 9px;
  padding: 0 5px;
  border-radius: 4px;
  line-height: 16px;
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.12);
}

.dark .cmd-kind {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.2);
}

.cmd-state-error {
  color: #dc2626;
}

.dark .cmd-desc {
  color: var(--text-secondary-dark, #9ca3af);
}

.ref-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 240px;
  padding: 3px 6px 3px 8px;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.35);
  color: #1d4ed8;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dark .ref-chip {
  background: rgba(59, 130, 246, 0.18);
  color: #93c5fd;
}

.ref-chip-remove {
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 0 2px;
  border-radius: 4px;
}

.ref-chip-remove:hover {
  background: rgba(59, 130, 246, 0.2);
}
</style>
