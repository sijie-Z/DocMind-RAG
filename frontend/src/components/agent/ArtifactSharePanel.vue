<template>
  <!-- 工件分享对话框（批次九）：创建/轮换、复制链接、撤销、过期选择。
       口径：链接绑定**当前这一版工件内容**；token 只在创建/轮换响应中出现一次，
       关闭后不再可读——所以创建成功后必须立刻复制。
       用 fixed 覆盖层（不依赖 naive-ui 全局注册），窄右栏里也能弹出完整对话框。 -->
  <div
    v-if="show"
    class="artifact-share-overlay"
    data-testid="artifact-share-panel"
    @click.self="$emit('update:show', false)"
  >
    <div class="artifact-share-dialog" role="dialog" aria-modal="true" :aria-label="t('artifacts.share.title')">
      <div class="artifact-share-header">
        <span class="artifact-share-title">{{ t('artifacts.share.title') }}</span>
        <button
          class="artifact-share-close"
          data-testid="artifact-share-close"
          :title="t('common.close')"
          @click="$emit('update:show', false)"
        >
          ×
        </button>
      </div>

      <p v-if="artifact" class="artifact-share-subject">
        {{ artifact.title }} · v{{ artifact.version_no }}
      </p>

      <!-- 公开链接的安全提示必须始终可见：分享 = 任何持链接者可读。 -->
      <p class="artifact-share-warning" data-testid="artifact-share-warning">
        {{ t('artifacts.share.warning') }}
      </p>

      <div v-if="loading" class="artifact-share-state" role="status">
        {{ t('artifacts.share.loading') }}
      </div>
      <template v-else>
        <div v-if="token" class="artifact-share-link-box">
          <label class="artifact-share-label" for="artifact-share-link">
            {{ t('artifacts.share.linkLabel') }}
          </label>
          <input
            id="artifact-share-link"
            data-testid="artifact-share-link"
            class="artifact-share-link"
            :value="link"
            readonly
          />
          <p class="artifact-share-once">{{ t('artifacts.share.onceNotice') }}</p>
        </div>
        <div v-else-if="shared" class="artifact-share-active" data-testid="artifact-share-active">
          {{ t('artifacts.share.activeNotice') }}
        </div>
        <div
          v-else-if="expired"
          class="artifact-share-state artifact-share-expired"
          data-testid="artifact-share-expired"
        >
          {{ t('artifacts.share.expiredNotice') }}
        </div>
        <div
          v-else-if="revoked"
          class="artifact-share-state"
          data-testid="artifact-share-revoked"
        >
          {{ t('artifacts.share.revokedNotice') }}
        </div>
        <div v-else class="artifact-share-state" data-testid="artifact-share-inactive">
          {{ t('artifacts.share.inactiveNotice') }}
        </div>

        <!-- 访问统计（批次十一）：只显示聚合次数与最后访问时间，不暴露访问者。 -->
        <div v-if="hasShare" class="artifact-share-stats" data-testid="artifact-share-stats">
          <span data-testid="artifact-share-access-count">
            {{ t('artifacts.share.accessCount', { count: accessCount }) }}
          </span>
          <span data-testid="artifact-share-last-accessed">
            {{
              lastAccessedAt
                ? t('artifacts.share.lastAccessed', { time: formatTime(lastAccessedAt) })
                : t('artifacts.share.neverAccessed')
            }}
          </span>
        </div>

        <div class="artifact-share-expiry-row">
          <label class="artifact-share-label" for="artifact-share-expiry">
            {{ t('artifacts.share.expiryLabel') }}
          </label>
          <select
            id="artifact-share-expiry"
            v-model.number="expiryChoice"
            data-testid="artifact-share-expiry"
            class="artifact-share-expiry"
          >
            <option :value="0">{{ t('artifacts.share.expiryNever') }}</option>
            <option :value="7">{{ t('artifacts.share.expiryDays', { days: 7 }) }}</option>
            <option :value="30">{{ t('artifacts.share.expiryDays', { days: 30 }) }}</option>
            <option :value="90">{{ t('artifacts.share.expiryDays', { days: 90 }) }}</option>
          </select>
        </div>
        <p v-if="expiresAt" class="artifact-share-expiry-hint" data-testid="artifact-share-expires-at">
          {{ t('artifacts.share.expiresAt', { time: formatTime(expiresAt) }) }}
        </p>
        <p
          v-if="revokedAt && !shared"
          class="artifact-share-expiry-hint"
          data-testid="artifact-share-revoked-at"
        >
          {{ t('artifacts.share.revokedAt', { time: formatTime(revokedAt) }) }}
        </p>

        <div class="artifact-share-actions">
          <button
            class="artifact-share-btn primary"
            data-testid="artifact-share-create"
            :disabled="busy"
            @click="createShare"
          >
            {{ shared ? t('artifacts.share.regenerate') : t('artifacts.share.create') }}
          </button>
          <button
            class="artifact-share-btn"
            data-testid="artifact-share-copy"
            :disabled="!token"
            @click="copyLink"
          >
            {{ t('artifacts.share.copy') }}
          </button>
          <button
            class="artifact-share-btn danger"
            data-testid="artifact-share-revoke"
            :disabled="!shared || busy"
            @click="revokeShare"
          >
            {{ t('artifacts.share.revoke') }}
          </button>
        </div>

        <p v-if="copyState === 'copied'" class="artifact-share-ok" role="status">
          {{ t('artifacts.share.copied') }}
        </p>
        <p v-if="copyState === 'failed'" class="artifact-share-error" role="alert">
          {{ t('artifacts.share.copyFailed') }}
        </p>
        <p v-if="error" class="artifact-share-error" data-testid="artifact-share-error" role="alert">
          {{ error }}
        </p>
      </template>

      <!-- 我的有效分享（批次十一）：跨工件的当前链接列表；只显示展示字段。 -->
      <section class="artifact-share-list" data-testid="artifact-share-list">
        <div class="artifact-share-list-header">
          <span class="artifact-share-label">{{ t('artifacts.share.mySharesTitle') }}</span>
          <button
            class="artifact-share-list-refresh"
            data-testid="artifact-share-list-refresh"
            :disabled="sharesLoading"
            @click="refreshAll"
          >
            {{ t('common.refresh') }}
          </button>
        </div>
        <div v-if="sharesLoading" class="artifact-share-state" data-testid="artifact-share-list-loading">
          {{ t('common.loading') }}
        </div>
        <template v-else>
          <div
            v-if="sharesError"
            class="artifact-share-error"
            data-testid="artifact-share-list-error"
            role="alert"
          >
            {{ sharesError }}
          </div>
          <div
            v-if="shares.length === 0 && !sharesError"
            class="artifact-share-state"
            data-testid="artifact-share-list-empty"
          >
            {{ t('artifacts.share.mySharesEmpty') }}
          </div>
          <ul v-if="shares.length > 0" class="artifact-share-list-items">
            <li
              v-for="item in shares"
              :key="item.share_id"
              class="artifact-share-list-item"
              :data-testid="`artifact-share-list-item-${item.artifact_id}`"
            >
              <div class="artifact-share-list-main">
                <span class="artifact-share-list-title">{{ item.title }}</span>
                <span class="artifact-share-list-meta">
                  v{{ item.version_no }} · {{ formatSize(item.file_size) }}
                </span>
              </div>
              <div class="artifact-share-list-meta">
                <span data-testid="artifact-share-list-created">
                  {{ t('artifacts.share.mySharesCreatedAt', { time: formatTime(item.created_at) }) }}
                </span>
                <span data-testid="artifact-share-list-expires">
                  {{
                    item.expires_at
                      ? t('artifacts.share.mySharesExpiresAt', {
                          time: formatTime(item.expires_at),
                        })
                      : t('artifacts.share.mySharesNeverExpires')
                  }}
                </span>
              </div>
              <div class="artifact-share-list-meta">
                <span data-testid="artifact-share-list-access-count">
                  {{ t('artifacts.share.accessCount', { count: item.access_count }) }}
                </span>
                <span data-testid="artifact-share-list-last-accessed">
                  {{
                    item.last_accessed_at
                      ? t('artifacts.share.lastAccessed', {
                          time: formatTime(item.last_accessed_at),
                        })
                      : t('artifacts.share.neverAccessed')
                  }}
                </span>
                <button
                  class="artifact-share-btn danger"
                  :data-testid="`artifact-share-list-revoke-${item.artifact_id}`"
                  :disabled="revokingId === item.share_id"
                  @click="revokeFromList(item)"
                >
                  {{ t('artifacts.share.revoke') }}
                </button>
              </div>
            </li>
          </ul>
        </template>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { artifactsApi } from '@/api/artifacts'
import type { AgentArtifact, ArtifactActiveShare } from '@/types/artifact'

const props = defineProps<{
  show: boolean
  artifact: AgentArtifact | null
}>()

defineEmits<{ 'update:show': [value: boolean] }>()

const { t } = useI18n()

const shared = ref(false)
const token = ref('')
const createdAt = ref<string | null>(null)
const expiresAt = ref<string | null>(null)
const revokedAt = ref<string | null>(null)
const accessCount = ref(0)
const lastAccessedAt = ref<string | null>(null)
const expiryChoice = ref(0)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const copyState = ref<'idle' | 'copied' | 'failed'>('idle')
const shares = ref<ArtifactActiveShare[]>([])
const sharesLoading = ref(false)
const sharesError = ref('')
const revokingId = ref('')

/** 是否存在分享行（含已过期/已撤销）：决定是否显示访问统计。 */
const hasShare = computed(() => !!createdAt.value || shared.value)

const link = computed(() =>
  token.value ? `${window.location.origin}/share/artifact/${token.value}` : '',
)

/** 失效态区分：过期 ≠ 撤销 ≠ 从未分享（主状态文案必须与事实一致）。 */
const expired = computed(() => {
  if (shared.value || !expiresAt.value) return false
  const time = new Date(expiresAt.value).getTime()
  return !Number.isNaN(time) && time <= Date.now()
})
const revoked = computed(() => !shared.value && !expired.value && !!revokedAt.value)

/**
 * 从现有过期时间回填下拉选择，避免「点了重新生成 → 静默变成永久链接」。
 * 取不小于剩余天数的档位；已过期/无过期 → 不过期（用户需显式选择）。
 */
function expiryChoiceFrom(value: string | null): number {
  if (!value) return 0
  const remaining = new Date(value).getTime() - Date.now()
  if (Number.isNaN(remaining) || remaining <= 0) return 0
  const days = Math.ceil(remaining / 86_400_000)
  if (days <= 7) return 7
  if (days <= 30) return 30
  return 90
}

function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatSize(bytes: number): string {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

let statusSeq = 0
let sharesSeq = 0

async function loadStatus() {
  if (!props.artifact) return
  const seq = ++statusSeq
  loading.value = true
  error.value = ''
  token.value = ''
  copyState.value = 'idle'
  try {
    const res = await artifactsApi.getShare(props.artifact.id)
    if (seq !== statusSeq) return
    const data = res.data?.data
    shared.value = !!data?.shared
    createdAt.value = data?.created_at ?? null
    expiresAt.value = data?.expires_at ?? null
    revokedAt.value = data?.revoked_at ?? null
    accessCount.value = data?.access_count ?? 0
    lastAccessedAt.value = data?.last_accessed_at ?? null
    expiryChoice.value = expiryChoiceFrom(expiresAt.value)
  } catch {
    if (seq !== statusSeq) return
    // 状态拉取失败不等于未分享：保留错误提示，禁用依赖状态的推断。
    // 同时清空上一工件的过期/撤销/统计，防止跨工件复用显示陈旧状态。
    shared.value = false
    createdAt.value = null
    expiresAt.value = null
    revokedAt.value = null
    accessCount.value = 0
    lastAccessedAt.value = null
    error.value = t('artifacts.share.loadFailed')
  } finally {
    if (seq === statusSeq) loading.value = false
  }
}

/** 我的有效分享列表：跨工件；失败只影响列表区，不影响当前工件状态。 */
async function loadShares() {
  const seq = ++sharesSeq
  sharesLoading.value = true
  sharesError.value = ''
  try {
    const res = await artifactsApi.listShares()
    if (seq !== sharesSeq) return
    shares.value = res.data?.data?.items || []
  } catch {
    if (seq !== sharesSeq) return
    shares.value = []
    sharesError.value = t('artifacts.share.mySharesLoadFailed')
  } finally {
    if (seq === sharesSeq) sharesLoading.value = false
  }
}

/** 刷新：当前工件状态与我的分享列表一起重读（计数/最后访问随之更新）。 */
function refreshAll() {
  void loadStatus()
  void loadShares()
}

watch(
  () => [props.show, props.artifact?.id],
  ([show]) => {
    if (show) {
      void loadStatus()
      void loadShares()
    }
  },
  { immediate: true },
)

async function createShare() {
  if (!props.artifact) return
  busy.value = true
  error.value = ''
  copyState.value = 'idle'
  try {
    const res = await artifactsApi.createShare(
      props.artifact.id,
      expiryChoice.value > 0 ? expiryChoice.value : null,
    )
    const data = res.data?.data
    token.value = data?.token || ''
    shared.value = true
    createdAt.value = data?.created_at ?? createdAt.value
    expiresAt.value = data?.expires_at ?? null
    revokedAt.value = data?.revoked_at ?? null
    accessCount.value = data?.access_count ?? 0
    lastAccessedAt.value = data?.last_accessed_at ?? null
    // 列表里该工件的条目/过期时间可能已变化（创建 vs 轮换）。
    void loadShares()
  } catch {
    error.value = t('artifacts.share.createFailed')
  } finally {
    busy.value = false
  }
}

async function copyLink() {
  if (!link.value) return
  try {
    await navigator.clipboard.writeText(link.value)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
}

async function revokeShare() {
  if (!props.artifact || !shared.value) return
  busy.value = true
  error.value = ''
  try {
    const res = await artifactsApi.revokeShare(props.artifact.id)
    applyRevoked(res.data?.data)
    copyState.value = 'idle'
    void loadShares()
  } catch {
    error.value = t('artifacts.share.revokeFailed')
  } finally {
    busy.value = false
  }
}

/** 撤销响应落回当前工件状态（计数保留，仅失效）。 */
function applyRevoked(
  data:
    | {
        access_count?: number
        last_accessed_at?: string | null
        expires_at?: string | null
        revoked_at?: string | null
      }
    | undefined,
) {
  shared.value = false
  token.value = ''
  expiresAt.value = data?.expires_at ?? null
  revokedAt.value = data?.revoked_at ?? null
  accessCount.value = data?.access_count ?? accessCount.value
  lastAccessedAt.value = data?.last_accessed_at ?? lastAccessedAt.value
}

/** 从「我的分享」列表撤销；若正好是当前工件，同步上方状态。 */
async function revokeFromList(item: ArtifactActiveShare) {
  revokingId.value = item.share_id
  sharesError.value = ''
  try {
    const res = await artifactsApi.revokeShare(item.artifact_id)
    if (props.artifact?.id === item.artifact_id) {
      applyRevoked(res.data?.data)
      copyState.value = 'idle'
    }
    await loadShares()
  } catch {
    sharesError.value = t('artifacts.share.mySharesRevokeFailed')
  } finally {
    revokingId.value = ''
  }
}
</script>

<style scoped>
.artifact-share-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(17, 24, 39, 0.55);
}

.artifact-share-dialog {
  width: 100%;
  max-width: 460px;
  max-height: 85vh;
  overflow-y: auto;
  padding: 16px;
  border-radius: 10px;
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.3);
}

.dark .artifact-share-dialog {
  background: var(--bg-primary-dark, #0f172a);
  color: var(--text-primary-dark, #f9fafb);
}

.artifact-share-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.artifact-share-title {
  font-size: 15px;
  font-weight: 600;
}

.artifact-share-close {
  background: none;
  border: none;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: var(--text-tertiary, #9ca3af);
}

.artifact-share-subject {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-secondary, #4b5563);
}

.artifact-share-warning {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid #fcd34d;
  background: #fffbeb;
  color: #92400e;
  font-size: 12px;
  line-height: 1.5;
}

.dark .artifact-share-warning {
  border-color: #78350f;
  background: rgba(120, 53, 15, 0.25);
  color: #fcd34d;
}

.artifact-share-state {
  font-size: 12px;
  color: var(--text-tertiary, #9ca3af);
  margin-bottom: 12px;
}

.artifact-share-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-bottom: 10px;
  font-size: 11px;
  color: var(--text-secondary, #4b5563);
}

.artifact-share-list {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color, #e5e7eb);
}

.dark .artifact-share-list {
  border-color: var(--border-color-dark, #374151);
}

.artifact-share-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.artifact-share-list-header .artifact-share-label {
  margin-bottom: 0;
}

.artifact-share-list-refresh {
  padding: 1px 6px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 4px;
  background: none;
  cursor: pointer;
  font-size: 10px;
  color: var(--text-secondary, #4b5563);
}

.artifact-share-list-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.artifact-share-list-items {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.artifact-share-list-item {
  padding: 8px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
}

.dark .artifact-share-list-item {
  border-color: var(--border-color-dark, #374151);
}

.artifact-share-list-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.artifact-share-list-title {
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-share-list-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.artifact-share-list-meta + .artifact-share-list-meta {
  margin-top: 4px;
}

.artifact-share-link-box {
  margin-bottom: 12px;
  padding: 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
}

.dark .artifact-share-link-box {
  border-color: var(--border-color-dark, #374151);
}

.artifact-share-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  margin-bottom: 4px;
  color: var(--text-secondary, #4b5563);
}

.artifact-share-link {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 4px;
  background: var(--bg-tertiary, #f3f4f6);
  font-size: 11px;
}

.artifact-share-once {
  margin: 6px 0 0;
  font-size: 11px;
  color: #b45309;
}

.artifact-share-active {
  margin-bottom: 12px;
  padding: 8px 10px;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
}

.dark .artifact-share-active {
  border-color: #1e3a8a;
  background: rgba(30, 58, 138, 0.25);
  color: #bfdbfe;
}

.artifact-share-expiry-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.artifact-share-expiry-row .artifact-share-label {
  margin-bottom: 0;
}

.artifact-share-expiry {
  padding: 2px 6px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 4px;
  background: var(--bg-primary, #fff);
  font-size: 12px;
}

.artifact-share-expiry-hint {
  margin: 0 0 8px;
  font-size: 11px;
  color: var(--text-tertiary, #9ca3af);
}

.artifact-share-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.artifact-share-btn {
  padding: 4px 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary, #4b5563);
}

.artifact-share-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.artifact-share-btn.primary {
  border-color: #2563eb;
  background: #2563eb;
  color: #fff;
}

.artifact-share-btn.danger {
  border-color: #dc2626;
  color: #dc2626;
}

.artifact-share-ok {
  margin: 8px 0 0;
  font-size: 12px;
  color: #15803d;
}

.artifact-share-error {
  margin: 8px 0 0;
  font-size: 12px;
  color: #dc2626;
}

.artifact-share-expired {
  color: #b45309;
}
</style>
