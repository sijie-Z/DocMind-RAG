<template>
  <!-- 公开知识库发布管理（批次十三）：挂在组织设置页（requiresAdmin）。
       安全口径：发布 = 任何持链接者可检索**显式白名单**内的文档；
       token 只在创建/轮换响应出现一次，关闭对话框后不可再读。 -->
  <section class="public-kb-panel" data-testid="public-kb-panel">
    <header class="public-kb-header">
      <div>
        <h2 class="public-kb-title">{{ t('publicKb.title') }}</h2>
        <p class="public-kb-subtitle">{{ t('publicKb.subtitle') }}</p>
      </div>
      <button class="public-kb-btn primary" data-testid="public-kb-create" @click="openCreate">
        {{ t('publicKb.create') }}
      </button>
    </header>

    <p class="public-kb-warning" data-testid="public-kb-warning">{{ t('publicKb.warning') }}</p>

    <div v-if="loading" class="public-kb-state" role="status" data-testid="public-kb-loading">
      {{ t('publicKb.loading') }}
    </div>
    <div v-else-if="loadError" class="public-kb-error" role="alert" data-testid="public-kb-load-error">
      <span>{{ loadError }}</span>
      <button class="public-kb-btn" @click="refreshAll">{{ t('publicKb.retry') }}</button>
    </div>
    <div
      v-else-if="publications.length === 0"
      class="public-kb-state"
      data-testid="public-kb-empty"
    >
      {{ t('publicKb.empty') }}
    </div>
    <div v-else class="public-kb-table-wrap">
      <table class="public-kb-table">
        <thead>
          <tr>
            <th>{{ t('publicKb.columns.title') }}</th>
            <th>{{ t('publicKb.columns.knowledgeBase') }}</th>
            <th>{{ t('publicKb.columns.status') }}</th>
            <th>{{ t('publicKb.columns.documents') }}</th>
            <th>{{ t('publicKb.columns.chat') }}</th>
            <th>{{ t('publicKb.columns.expires') }}</th>
            <th>{{ t('publicKb.columns.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in publications"
            :key="item.id"
            :data-testid="`public-kb-publication-${item.id}`"
          >
            <td class="public-kb-cell-title">{{ item.title }}</td>
            <td>{{ item.knowledge_base_name || item.knowledge_base_id }}</td>
            <td>
              <span class="public-kb-status" :class="`is-${publicationStatus(item)}`">
                {{ statusLabel(item) }}
              </span>
            </td>
            <td>{{ item.document_count }}</td>
            <td>
              <span
                class="public-kb-status"
                :class="item.chat_enabled ? 'is-active' : 'is-inactive'"
                :data-testid="`public-kb-chat-status-${item.id}`"
              >
                {{
                  item.chat_enabled ? t('publicKb.chatEnabled') : t('publicKb.chatDisabled')
                }}
              </span>
            </td>
            <td>
              {{
                item.expires_at
                  ? t('publicKb.expiresAt', { time: formatTime(item.expires_at) })
                  : t('publicKb.neverExpires')
              }}
            </td>
            <td class="public-kb-actions">
              <button
                class="public-kb-btn"
                :data-testid="`public-kb-manage-${item.id}`"
                @click="openManage(item)"
              >
                {{ t('publicKb.actions.manage') }}
              </button>
              <button
                v-if="item.active"
                class="public-kb-btn"
                :data-testid="`public-kb-rotate-${item.id}`"
                @click="openRotate(item)"
              >
                {{ t('publicKb.actions.rotate') }}
              </button>
              <button
                v-if="item.active"
                class="public-kb-btn danger"
                :data-testid="`public-kb-revoke-${item.id}`"
                @click="revokeTarget = item"
              >
                {{ t('publicKb.actions.revoke') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="notice" class="public-kb-ok" role="status" data-testid="public-kb-notice">{{ notice }}</p>

    <!-- 发布对话框：标题/描述/有效期/白名单多选 -->
    <div
      v-if="createOpen"
      class="public-kb-overlay"
      data-testid="public-kb-create-dialog"
      @click.self="closeCreate"
    >
      <div class="public-kb-dialog" role="dialog" aria-modal="true">
        <div class="public-kb-dialog-header">
          <span>{{ t('publicKb.createTitle') }}</span>
          <button class="public-kb-close" data-testid="public-kb-create-close" @click="closeCreate">
            ×
          </button>
        </div>

        <template v-if="createdLink">
          <p class="public-kb-warning">{{ t('publicKb.onceNotice') }}</p>
          <label class="public-kb-label" for="public-kb-created-link">
            {{ t('publicKb.linkLabel') }}
          </label>
          <input
            id="public-kb-created-link"
            class="public-kb-input"
            data-testid="public-kb-created-link"
            :value="createdLink"
            readonly
          />
          <div class="public-kb-dialog-actions">
            <button class="public-kb-btn primary" data-testid="public-kb-copy" @click="copyLink(createdLink)">
              {{ copyState === 'copied' ? t('publicKb.copied') : t('publicKb.copy') }}
            </button>
            <button class="public-kb-btn" data-testid="public-kb-created-done" @click="closeCreate">
              {{ t('publicKb.done') }}
            </button>
          </div>
          <p v-if="copyState === 'failed'" class="public-kb-error" role="alert">
            {{ t('publicKb.copyFailed') }}
          </p>
        </template>

        <form v-else @submit.prevent="submitCreate">
          <label class="public-kb-label" for="public-kb-form-kb">{{ t('publicKb.form.knowledgeBase') }}</label>
          <select
            id="public-kb-form-kb"
            v-model="createForm.kbId"
            class="public-kb-input"
            data-testid="public-kb-form-kb"
            @change="loadCreateCandidates"
          >
            <option value="">{{ t('publicKb.form.knowledgeBasePlaceholder') }}</option>
            <option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>

          <label class="public-kb-label" for="public-kb-form-title">{{ t('publicKb.form.title') }}</label>
          <input
            id="public-kb-form-title"
            v-model="createForm.title"
            class="public-kb-input"
            data-testid="public-kb-form-title"
            maxlength="100"
          />

          <label class="public-kb-label" for="public-kb-form-description">
            {{ t('publicKb.form.description') }}
          </label>
          <textarea
            id="public-kb-form-description"
            v-model="createForm.description"
            class="public-kb-input public-kb-textarea"
            data-testid="public-kb-form-description"
            maxlength="500"
          />

          <label class="public-kb-label" for="public-kb-form-expiry">{{ t('publicKb.form.expiry') }}</label>
          <select
            id="public-kb-form-expiry"
            v-model.number="createForm.expiryDays"
            class="public-kb-input"
            data-testid="public-kb-form-expiry"
          >
            <option :value="0">{{ t('publicKb.expiry.never') }}</option>
            <option :value="7">{{ t('publicKb.expiry.days', { days: 7 }) }}</option>
            <option :value="30">{{ t('publicKb.expiry.days', { days: 30 }) }}</option>
            <option :value="90">{{ t('publicKb.expiry.days', { days: 90 }) }}</option>
          </select>

          <p class="public-kb-label">{{ t('publicKb.form.documents') }}</p>
          <p class="public-kb-hint">{{ t('publicKb.form.documentsHint') }}</p>
          <div
            v-if="createCandidatesLoading"
            class="public-kb-state"
            data-testid="public-kb-candidates-loading"
          >
            {{ t('publicKb.loading') }}
          </div>
          <div v-else-if="createCandidates.length === 0 && createForm.kbId" class="public-kb-state">
            {{ t('publicKb.form.noDocuments') }}
          </div>
          <div v-else class="public-kb-candidate-list" data-testid="public-kb-candidates">
            <label v-for="doc in createCandidates" :key="doc.id" class="public-kb-candidate">
              <input
                v-model="createSelected"
                type="checkbox"
                :value="doc.id"
                :data-testid="`public-kb-candidate-${doc.id}`"
              />
              <span>{{ doc.title }}</span>
            </label>
            <p v-if="createCandidatesTruncated" class="public-kb-hint">
              {{ t('publicKb.candidatesTruncated') }}
            </p>
          </div>

          <p v-if="createError" class="public-kb-error" role="alert" data-testid="public-kb-create-error">
            {{ createError }}
          </p>
          <div class="public-kb-dialog-actions">
            <button class="public-kb-btn" type="button" @click="closeCreate">
              {{ t('publicKb.cancel') }}
            </button>
            <button
              class="public-kb-btn primary"
              type="submit"
              data-testid="public-kb-create-submit"
              :disabled="createBusy"
            >
              {{ createBusy ? t('publicKb.creating') : t('publicKb.submit') }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- 管理白名单 -->
    <div
      v-if="manageTarget"
      class="public-kb-overlay"
      data-testid="public-kb-manage-dialog"
      @click.self="closeManage"
    >
      <div class="public-kb-dialog" role="dialog" aria-modal="true">
        <div class="public-kb-dialog-header">
          <span>{{ t('publicKb.manageTitle') }}</span>
          <button class="public-kb-close" data-testid="public-kb-manage-close" @click="closeManage">
            ×
          </button>
        </div>
        <p class="public-kb-hint">{{ t('publicKb.manageHint') }}</p>
        <div v-if="manageLoading" class="public-kb-state" role="status">{{ t('publicKb.loading') }}</div>
        <template v-else>
          <div v-if="manageError" class="public-kb-error" role="alert">{{ manageError }}</div>
          <div class="public-kb-candidate-list" data-testid="public-kb-manage-list">
            <label v-for="doc in manageRows" :key="doc.id" class="public-kb-candidate">
              <input
                v-model="manageSelected"
                type="checkbox"
                :value="doc.id"
                :data-testid="`public-kb-manage-doc-${doc.id}`"
              />
              <span>{{ doc.title }}</span>
              <span v-if="!doc.searchable" class="public-kb-badge is-stale">
                {{ t('publicKb.notSearchable') }}
              </span>
            </label>
            <p v-if="manageRows.length === 0" class="public-kb-state">
              {{ t('publicKb.form.noDocuments') }}
            </p>
            <p v-if="manageTruncated" class="public-kb-hint">{{ t('publicKb.candidatesTruncated') }}</p>
          </div>

          <!-- 批次十七：发布级聊天开关 + 日预算 -->
          <div class="public-kb-chat-settings" data-testid="public-kb-chat-settings">
            <p class="public-kb-label">{{ t('publicKb.chat.title') }}</p>
            <label class="public-kb-candidate">
              <input
                v-model="manageChatEnabled"
                type="checkbox"
                data-testid="public-kb-chat-enabled"
              />
              <span>{{ t('publicKb.chat.toggle') }}</span>
            </label>
            <p class="public-kb-hint">{{ t('publicKb.chat.hint') }}</p>
            <label class="public-kb-label" for="public-kb-chat-budget">
              {{ t('publicKb.chat.budget') }}
            </label>
            <input
              id="public-kb-chat-budget"
              v-model="manageChatBudget"
              class="public-kb-input"
              data-testid="public-kb-chat-budget"
              type="number"
              min="0"
              max="10000000"
              step="1"
              :placeholder="t('publicKb.chat.budgetPlaceholder')"
            />
            <p class="public-kb-hint">{{ t('publicKb.chat.budgetHint') }}</p>
            <p
              v-if="chatSettingsError"
              class="public-kb-error"
              role="alert"
              data-testid="public-kb-chat-settings-error"
            >
              {{ chatSettingsError }}
            </p>
            <button
              class="public-kb-btn"
              type="button"
              data-testid="public-kb-chat-settings-save"
              :disabled="chatSettingsSaving || manageSaving"
              @click="saveChatSettings"
            >
              {{ chatSettingsSaving ? t('publicKb.chat.saving') : t('publicKb.chat.save') }}
            </button>
          </div>

          <!-- 批次十八：仅在发布已开启匿名聊天且发布预算非 0 时提供 iframe
               嵌入代码（全局聊天开关是否开启不在列表接口投影内，见报告残余
               风险）。token 不进列表接口，需粘贴创建/轮换时拿到的公开链接。 -->
          <PublicKbEmbedCode
            v-if="manageTarget?.chat_enabled && manageTarget?.daily_token_budget !== 0"
            :title="manageTarget?.title"
          />
        </template>
        <div class="public-kb-dialog-actions">
          <button class="public-kb-btn" @click="closeManage">{{ t('publicKb.cancel') }}</button>
          <button
            class="public-kb-btn primary"
            data-testid="public-kb-manage-save"
            :disabled="manageSaving || manageLoading || chatSettingsSaving"
            @click="saveManage"
          >
            {{ manageSaving ? t('publicKb.saving') : t('publicKb.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 轮换确认 / 结果 -->
    <div
      v-if="rotateTarget"
      class="public-kb-overlay"
      data-testid="public-kb-rotate-dialog"
      @click.self="closeRotate"
    >
      <div class="public-kb-dialog" role="dialog" aria-modal="true">
        <div class="public-kb-dialog-header">
          <span>{{ t('publicKb.rotateTitle') }}</span>
          <button
            class="public-kb-close"
            data-testid="public-kb-rotate-close"
            :disabled="rotateBusy"
            @click="closeRotate"
          >
            ×
          </button>
        </div>
        <template v-if="rotatedLink">
          <p class="public-kb-warning">{{ t('publicKb.onceNotice') }}</p>
          <input
            class="public-kb-input"
            data-testid="public-kb-rotated-link"
            :value="rotatedLink"
            readonly
          />
          <PublicKbEmbedCode
            v-if="rotateTarget.chat_enabled && rotateTarget.daily_token_budget !== 0"
            :link="rotatedLink"
            :title="rotateTarget.title"
          />
          <div class="public-kb-dialog-actions">
            <button class="public-kb-btn primary" @click="copyLink(rotatedLink)">
              {{ copyState === 'copied' ? t('publicKb.copied') : t('publicKb.copy') }}
            </button>
            <button class="public-kb-btn" @click="closeRotate">{{ t('publicKb.done') }}</button>
          </div>
        </template>
        <template v-else>
          <p class="public-kb-hint">{{ t('publicKb.rotateConfirm') }}</p>
          <p v-if="rotateError" class="public-kb-error" role="alert">{{ rotateError }}</p>
          <div class="public-kb-dialog-actions">
            <button class="public-kb-btn" :disabled="rotateBusy" @click="closeRotate">
              {{ t('publicKb.cancel') }}
            </button>
            <button
              class="public-kb-btn primary"
              data-testid="public-kb-rotate-confirm"
              :disabled="rotateBusy"
              @click="confirmRotate"
            >
              {{ rotateBusy ? t('publicKb.rotating') : t('publicKb.actions.rotate') }}
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- 撤销确认 -->
    <div
      v-if="revokeTarget"
      class="public-kb-overlay"
      data-testid="public-kb-revoke-dialog"
      @click.self="revokeTarget = null"
    >
      <div class="public-kb-dialog" role="dialog" aria-modal="true">
        <div class="public-kb-dialog-header">
          <span>{{ t('publicKb.actions.revoke') }}</span>
          <button class="public-kb-close" @click="revokeTarget = null">×</button>
        </div>
        <p class="public-kb-hint">{{ t('publicKb.revokeConfirm') }}</p>
        <p v-if="revokeError" class="public-kb-error" role="alert">{{ revokeError }}</p>
        <div class="public-kb-dialog-actions">
          <button class="public-kb-btn" @click="revokeTarget = null">{{ t('publicKb.cancel') }}</button>
          <button
            class="public-kb-btn danger"
            data-testid="public-kb-revoke-confirm"
            :disabled="revokeBusy"
            @click="confirmRevoke"
          >
            {{ t('publicKb.actions.revoke') }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listKnowledgeBases, type KnowledgeBaseEntity } from '@/api/knowledgeBases'
import {
  publicKbApi,
  type KbCandidateDocument,
  type KbPublication,
} from '@/api/publicKb'
import PublicKbEmbedCode from './PublicKbEmbedCode.vue'

const { t } = useI18n()

const publications = ref<KbPublication[]>([])
const knowledgeBases = ref<KnowledgeBaseEntity[]>([])
const loading = ref(true)
const loadError = ref('')
const notice = ref('')

const createOpen = ref(false)
const createBusy = ref(false)
const createError = ref('')
const createForm = reactive({ kbId: '', title: '', description: '', expiryDays: 0 })
const createCandidates = ref<KbCandidateDocument[]>([])
const createCandidatesLoading = ref(false)
const createCandidatesTruncated = ref(false)
const createSelected = ref<string[]>([])
const createdLink = ref('')
const copyState = ref<'idle' | 'copied' | 'failed'>('idle')

const manageTarget = ref<KbPublication | null>(null)
const manageRows = ref<Array<{ id: string; title: string; searchable: boolean }>>([])
const manageSelected = ref<string[]>([])
const manageLoading = ref(false)
const manageSaving = ref(false)
const manageError = ref('')
const manageTruncated = ref(false)
const manageChatEnabled = ref(false)
// type=number 的 v-model 会把输入转成 number（空值保持字符串 ''），两者都要接受。
const manageChatBudget = ref<string | number>('')
const chatSettingsSaving = ref(false)
const chatSettingsError = ref('')
const MAX_CHAT_DAILY_BUDGET = 10_000_000
// 代际计数：管理对话框/轮换对话框关闭或切换目标后，旧请求的迟到响应
// 不得写入新目标的列表、token 或嵌入片段。
let manageGeneration = 0
let rotateGeneration = 0
// 保存请求序号：旧请求的 finally 不得清掉新请求的 busy 标志。
let chatSaveToken = 0
let manageSaveToken = 0

const rotateTarget = ref<KbPublication | null>(null)
const rotateBusy = ref(false)
const rotateError = ref('')
const rotatedLink = ref('')

const revokeTarget = ref<KbPublication | null>(null)
const revokeBusy = ref(false)
const revokeError = ref('')

function formatTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function publicationStatus(item: KbPublication): string {
  if (item.revoked_at) return 'revoked'
  if (!item.enabled) return 'disabled'
  if (item.expires_at) {
    const time = new Date(item.expires_at).getTime()
    if (!Number.isNaN(time) && time <= Date.now()) return 'expired'
  }
  return item.active ? 'active' : 'inactive'
}

function statusLabel(item: KbPublication): string {
  const status = publicationStatus(item)
  const map: Record<string, string> = {
    active: t('publicKb.statusActive'),
    revoked: t('publicKb.statusRevoked'),
    expired: t('publicKb.statusExpired'),
    disabled: t('publicKb.statusDisabled'),
    inactive: t('publicKb.statusInactive'),
  }
  return map[status] || status
}

function absoluteLink(path: string): string {
  if (!path) return ''
  return `${window.location.origin}${path}`
}

async function loadKnowledgeBases() {
  if (knowledgeBases.value.length > 0) return
  try {
    const result = await listKnowledgeBases({ pageSize: 100, silent: true })
    knowledgeBases.value = result.data
  } catch {
    // KB 列表失败不阻塞已有发布的展示；创建对话框会显示空选择并提示。
    knowledgeBases.value = []
  }
}

async function refreshAll() {
  loading.value = true
  loadError.value = ''
  try {
    publications.value = await publicKbApi.list()
  } catch {
    publications.value = []
    loadError.value = t('publicKb.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void refreshAll()
  void loadKnowledgeBases()
})

function openCreate() {
  createOpen.value = true
  createBusy.value = false
  createError.value = ''
  createdLink.value = ''
  copyState.value = 'idle'
  createForm.kbId = ''
  createForm.title = ''
  createForm.description = ''
  createForm.expiryDays = 0
  createCandidates.value = []
  createCandidatesTruncated.value = false
  createSelected.value = []
  void loadKnowledgeBases()
}

function closeCreate() {
  createOpen.value = false
  createdLink.value = ''
}

async function loadCreateCandidates() {
  createCandidates.value = []
  createSelected.value = []
  createCandidatesTruncated.value = false
  if (!createForm.kbId) return
  createCandidatesLoading.value = true
  try {
    const result = await publicKbApi.candidates(createForm.kbId)
    createCandidates.value = result.documents
    createCandidatesTruncated.value = !!result.truncated
  } catch {
    createError.value = t('publicKb.loadFailed')
  } finally {
    createCandidatesLoading.value = false
  }
}

async function submitCreate() {
  createError.value = ''
  if (!createForm.kbId || !createForm.title.trim()) {
    createError.value = t('publicKb.form.required')
    return
  }
  createBusy.value = true
  try {
    const created = await publicKbApi.create({
      knowledge_base_id: createForm.kbId,
      title: createForm.title.trim(),
      description: createForm.description.trim() || null,
      expires_in_days: createForm.expiryDays > 0 ? createForm.expiryDays : null,
      document_ids: createSelected.value,
    })
    createdLink.value = absoluteLink(created.url)
    notice.value = t('publicKb.createdNotice')
    await refreshAll()
  } catch {
    createError.value = t('publicKb.operationFailed')
  } finally {
    createBusy.value = false
  }
}

async function openManage(item: KbPublication) {
  const generation = ++manageGeneration
  manageTarget.value = item
  manageLoading.value = true
  manageError.value = ''
  manageRows.value = []
  manageSelected.value = []
  manageTruncated.value = false
  manageChatEnabled.value = !!item.chat_enabled
  manageChatBudget.value =
    item.daily_token_budget === null || item.daily_token_budget === undefined
      ? ''
      : String(item.daily_token_budget)
  // 保存请求可能仍在飞行（关闭后重开同一发布）：保留 busy 状态，
  // 避免并发提交让旧请求最后落库。
  chatSettingsError.value = ''
  try {
    const [published, candidates] = await Promise.all([
      publicKbApi.documents(item.id),
      publicKbApi.candidates(item.knowledge_base_id),
    ])
    // 慢请求 + 关闭/切换目标：不得把 A 的白名单写进 B 的对话框。
    if (generation !== manageGeneration || manageTarget.value?.id !== item.id) return
    const rows = new Map<string, { id: string; title: string; searchable: boolean }>()
    for (const doc of candidates.documents) {
      rows.set(doc.id, { id: doc.id, title: doc.title, searchable: true })
    }
    for (const doc of published.documents) {
      rows.set(doc.id, { id: doc.id, title: doc.title, searchable: doc.searchable })
    }
    manageRows.value = [...rows.values()]
    manageSelected.value = published.documents.map((doc) => doc.id)
    manageTruncated.value = !!candidates.truncated
  } catch {
    if (generation === manageGeneration && manageTarget.value?.id === item.id) {
      manageError.value = t('publicKb.loadFailed')
    }
  } finally {
    if (generation === manageGeneration && manageTarget.value?.id === item.id) {
      manageLoading.value = false
    }
  }
}

function closeManage() {
  manageGeneration += 1
  manageTarget.value = null
  manageRows.value = []
  manageSelected.value = []
  manageChatEnabled.value = false
  manageChatBudget.value = ''
  chatSettingsError.value = ''
}

async function saveChatSettings() {
  if (!manageTarget.value || chatSettingsSaving.value || manageSaving.value) return
  const generation = manageGeneration
  const requestToken = ++chatSaveToken
  const targetId = manageTarget.value.id
  const raw =
    typeof manageChatBudget.value === 'number'
      ? String(manageChatBudget.value)
      : manageChatBudget.value.trim()
  let budget: number | null = null
  if (raw !== '') {
    if (!/^\d+$/.test(raw)) {
      chatSettingsError.value = t('publicKb.chat.budgetInvalid')
      return
    }
    budget = Number(raw)
    if (!Number.isSafeInteger(budget) || budget < 0 || budget > MAX_CHAT_DAILY_BUDGET) {
      chatSettingsError.value = t('publicKb.chat.budgetInvalid')
      return
    }
  }
  chatSettingsSaving.value = true
  chatSettingsError.value = ''
  try {
    const updated = await publicKbApi.setChatSettings(targetId, {
      chat_enabled: manageChatEnabled.value,
      daily_token_budget: budget,
    })
    // 服务端已生效：无论对话框是否仍指向原目标，都刷新列表真值。
    await refreshAll()
    // 慢请求 + 关闭/重开对话框时，不得改写新目标（含同一发布的新会话）的字段。
    if (generation !== manageGeneration || manageTarget.value?.id !== targetId) return
    manageTarget.value = updated
    manageChatEnabled.value = !!updated.chat_enabled
    manageChatBudget.value =
      updated.daily_token_budget === null || updated.daily_token_budget === undefined
        ? ''
        : String(updated.daily_token_budget)
    notice.value = t('publicKb.chat.saved')
  } catch {
    if (generation === manageGeneration && manageTarget.value?.id === targetId) {
      chatSettingsError.value = t('publicKb.operationFailed')
    }
  } finally {
    // 旧请求不得清掉新请求的 busy 标志。
    if (requestToken === chatSaveToken) chatSettingsSaving.value = false
  }
}

async function saveManage() {
  if (!manageTarget.value || manageSaving.value || chatSettingsSaving.value) return
  const generation = manageGeneration
  const requestToken = ++manageSaveToken
  const targetId = manageTarget.value.id
  manageSaving.value = true
  manageError.value = ''
  try {
    await publicKbApi.setDocuments(targetId, 'replace', manageSelected.value)
    await refreshAll()
    if (generation !== manageGeneration || manageTarget.value?.id !== targetId) return
    notice.value = t('publicKb.saved')
    closeManage()
  } catch {
    if (generation === manageGeneration && manageTarget.value?.id === targetId) {
      manageError.value = t('publicKb.operationFailed')
    }
  } finally {
    if (requestToken === manageSaveToken) manageSaving.value = false
  }
}

function openRotate(item: KbPublication) {
  // 轮换请求在飞行中时不得切换目标：旧链接已被服务端作废，新 token
  // 只返回一次，切走会永久丢失它。
  if (rotateBusy.value) return
  rotateGeneration += 1
  rotateTarget.value = item
  rotatedLink.value = ''
  rotateError.value = ''
  copyState.value = 'idle'
}

function closeRotate() {
  // 轮换进行中关闭会丢失一次性返回的新 token（旧链接已失效），禁止。
  if (rotateBusy.value) return
  rotateGeneration += 1
  rotateTarget.value = null
  rotatedLink.value = ''
  rotateError.value = ''
}

async function confirmRotate() {
  const target = rotateTarget.value
  if (!target || rotateBusy.value) return
  const generation = rotateGeneration
  const targetId = target.id
  rotateBusy.value = true
  rotateError.value = ''
  try {
    const rotated = await publicKbApi.rotate(targetId, null)
    if (generation !== rotateGeneration || rotateTarget.value?.id !== targetId) return
    // 以服务端响应为准：聊天开关/预算/标题可能已变化，嵌入片段必须用新值。
    rotateTarget.value = rotated
    rotatedLink.value = absoluteLink(rotated.url)
    copyState.value = 'idle'
    // token 已展示，不再需要阻止关闭；列表刷新不应让 Done/Cancel 暂时失效。
    rotateBusy.value = false
    await refreshAll()
  } catch {
    if (generation === rotateGeneration && rotateTarget.value?.id === targetId) {
      rotateError.value = t('publicKb.operationFailed')
    }
  } finally {
    if (generation === rotateGeneration) rotateBusy.value = false
  }
}

async function confirmRevoke() {
  if (!revokeTarget.value) return
  revokeBusy.value = true
  revokeError.value = ''
  try {
    await publicKbApi.revoke(revokeTarget.value.id)
    notice.value = t('publicKb.revokedNotice')
    revokeTarget.value = null
    await refreshAll()
  } catch {
    revokeError.value = t('publicKb.operationFailed')
  } finally {
    revokeBusy.value = false
  }
}

async function copyLink(link: string) {
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
}
</script>

<style scoped>
.public-kb-panel {
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  background: #fff;
  padding: 20px;
  margin-bottom: 24px;
}
.dark .public-kb-panel {
  border-color: #334155;
  background: #1e293b;
}
.public-kb-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.public-kb-title {
  font-size: 18px;
  font-weight: 700;
  color: #1e293b;
}
.dark .public-kb-title {
  color: #f1f5f9;
}
.public-kb-subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}
.public-kb-warning {
  margin: 14px 0;
  border: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13px;
}
.dark .public-kb-warning {
  border-color: #92400e;
  background: #451a03;
  color: #fcd34d;
}
.public-kb-hint {
  font-size: 12px;
  color: #64748b;
}
.dark .public-kb-hint {
  color: #94a3b8;
}
.public-kb-state {
  padding: 14px 0;
  font-size: 14px;
  color: #64748b;
}
.public-kb-error {
  color: #b91c1c;
  font-size: 13px;
}
.dark .public-kb-error {
  color: #fca5a5;
}
.public-kb-ok {
  color: #047857;
  font-size: 13px;
}
.dark .public-kb-ok {
  color: #6ee7b7;
}
.public-kb-table-wrap {
  overflow-x: auto;
}
.public-kb-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.public-kb-table th,
.public-kb-table td {
  padding: 10px 8px;
  border-bottom: 1px solid #e2e8f0;
  text-align: left;
  vertical-align: middle;
}
.dark .public-kb-table th,
.dark .public-kb-table td {
  border-color: #334155;
}
.public-kb-cell-title {
  font-weight: 600;
}
.public-kb-status {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  background: #f1f5f9;
  color: #475569;
}
.public-kb-status.is-active {
  background: #d1fae5;
  color: #047857;
}
.public-kb-status.is-revoked,
.public-kb-status.is-disabled {
  background: #fee2e2;
  color: #b91c1c;
}
.public-kb-status.is-expired {
  background: #fef3c7;
  color: #b45309;
}
.public-kb-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.public-kb-btn {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
}
.dark .public-kb-btn {
  border-color: #475569;
  background: #0f172a;
  color: #e2e8f0;
}
.public-kb-btn.primary {
  border-color: #2563eb;
  background: #2563eb;
  color: #fff;
}
.public-kb-btn.danger {
  border-color: #dc2626;
  background: #fff;
  color: #b91c1c;
}
.dark .public-kb-btn.danger {
  background: #0f172a;
  color: #fca5a5;
}
.public-kb-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.public-kb-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.public-kb-dialog {
  width: min(640px, 100%);
  max-height: 90vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 14px;
  padding: 18px;
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
}
.dark .public-kb-dialog {
  background: #0f172a;
  color: #e2e8f0;
}
.public-kb-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 700;
  margin-bottom: 12px;
}
.public-kb-close {
  border: none;
  background: transparent;
  font-size: 20px;
  cursor: pointer;
  color: #64748b;
}
.public-kb-label {
  display: block;
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}
.dark .public-kb-label {
  color: #cbd5e1;
}
.public-kb-input {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 13px;
  background: #fff;
  color: #0f172a;
}
.dark .public-kb-input {
  border-color: #475569;
  background: #020617;
  color: #e2e8f0;
}
.public-kb-textarea {
  min-height: 64px;
  resize: vertical;
}
.public-kb-candidate-list {
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px;
}
.dark .public-kb-candidate-list {
  border-color: #334155;
}
.public-kb-candidate {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 2px;
  font-size: 13px;
}
.public-kb-badge.is-stale {
  border-radius: 999px;
  background: #fef3c7;
  color: #b45309;
  padding: 1px 8px;
  font-size: 11px;
}
.public-kb-chat-settings {
  margin-top: 16px;
  border-top: 1px dashed #cbd5e1;
  padding-top: 8px;
}
.dark .public-kb-chat-settings {
  border-color: #475569;
}
.public-kb-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>
