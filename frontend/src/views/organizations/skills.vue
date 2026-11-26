<template>
  <div class="p-4 md:p-6">
    <div class="mx-auto max-w-6xl">
      <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.orgSkills') }}
          </h1>
          <p class="mt-1 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            <span class="h-0.5 w-6 rounded-full bg-emerald-600"></span>
            {{ t('orgSkills.subtitle') }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            class="w-40"
            data-testid="org-skill-status-filter"
            @update:value="applyFilter"
          />
          <n-button secondary round :loading="loading" data-testid="org-skill-refresh" @click="load">
            {{ t('orgSkills.refresh') }}
          </n-button>
        </div>
      </div>

      <n-alert type="info" :bordered="false" class="mb-4" data-testid="org-skill-scope-banner">
        {{ t('orgSkills.scopeBanner') }}
      </n-alert>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false" data-testid="org-skill-load-error">
        {{ loadError }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('orgSkills.retry') }}</n-button>
      </n-alert>

      <!-- 待审队列：跨技能汇总 pending 版本 -->
      <n-card
        v-if="pendingRows.length > 0 || pendingLoading"
        :title="t('orgSkills.pendingQueue.title')"
        class="mb-5"
        :bordered="false"
        data-testid="org-skill-pending-queue"
      >
        <p class="mb-3 text-xs text-slate-500 dark:text-slate-400">
          {{ t('orgSkills.pendingQueue.subtitle') }}
        </p>
        <n-spin :show="pendingLoading">
          <div class="space-y-2">
            <div
              v-for="item in pendingRows"
              :key="`${item.skill.id}:${item.version.version_no}`"
              class="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50/40 p-3 dark:border-amber-900/40 dark:bg-amber-950/10 sm:flex-row sm:items-center sm:justify-between"
              :data-testid="`org-skill-pending-${item.skill.id}-${item.version.version_no}`"
            >
              <div class="min-w-0">
                <div class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {{ item.skill.name }}
                  <span class="ml-1 text-xs font-normal text-slate-500 dark:text-slate-400">
                    v{{ item.version.version_no }} · {{ t(`orgSkills.source.${item.version.source}`) }}
                  </span>
                </div>
                <div class="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {{ t('orgSkills.pendingQueue.authorHint', { id: item.version.created_by }) }}
                </div>
                <div v-if="item.version.review_status === 'pending' && isAuthor(item.version)" class="mt-1 text-xs text-amber-600 dark:text-amber-400" data-testid="org-skill-self-review-hint">
                  {{ t('orgSkills.pendingQueue.authorCannotReview') }}
                </div>
                <div
                  class="mt-2 flex flex-wrap items-center gap-1"
                  :data-testid="`org-skill-pending-payload-${item.skill.id}-${item.version.version_no}`"
                >
                  <n-tag
                    v-for="(phrase, idx) in item.version.payload?.trigger_phrases || []"
                    :key="`p-${idx}-${phrase}`"
                    size="small"
                    :bordered="false"
                  >
                    {{ phrase }}
                  </n-tag>
                  <n-tag
                    v-for="(step, idx) in item.version.payload?.tool_sequence || []"
                    :key="`s-${idx}-${step.tool_name}`"
                    type="info"
                    size="small"
                    :bordered="false"
                  >
                    {{ step.tool_name }}
                  </n-tag>
                </div>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <n-button
                  size="small"
                  type="primary"
                  :disabled="!canReview(item.version)"
                  :loading="actionBusy === `approve-${item.skill.id}-${item.version.version_no}`"
                  :data-testid="`org-skill-approve-${item.skill.id}-${item.version.version_no}`"
                  @click="approveVersion(item.skill.id, item.version)"
                >
                  {{ t('orgSkills.pendingQueue.approve') }}
                </n-button>
                <n-button
                  size="small"
                  secondary
                  type="error"
                  :disabled="!canReview(item.version)"
                  :loading="actionBusy === `reject-${item.skill.id}-${item.version.version_no}`"
                  :data-testid="`org-skill-reject-${item.skill.id}-${item.version.version_no}`"
                  @click="rejectVersion(item.skill.id, item.version)"
                >
                  {{ t('orgSkills.pendingQueue.reject') }}
                </n-button>
              </div>
            </div>
          </div>
        </n-spin>
      </n-card>

      <n-spin :show="loading">
        <div
          v-if="!loading && !loadError && rows.length === 0"
          class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-16 dark:border-slate-700 dark:bg-slate-900"
          data-testid="org-skill-empty"
        >
          <h2 class="mb-2 text-lg font-semibold text-slate-800 dark:text-slate-100">
            {{ t('orgSkills.empty') }}
          </h2>
          <p class="max-w-md text-center text-sm leading-relaxed text-slate-500 dark:text-slate-400">
            {{ t('orgSkills.emptyHint') }}
          </p>
        </div>

        <div v-else-if="rows.length > 0" class="space-y-3">
          <div
            v-for="row in rows"
            :key="row.id"
            class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
            :data-testid="`org-skill-row-${row.id}`"
          >
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ row.name }}</span>
                  <n-tag :type="row.status === 'archived' ? 'default' : 'success'" size="small">
                    {{ t(`orgSkills.status.${row.status}`) }}
                  </n-tag>
                  <n-tag v-if="(row.pending_count || 0) > 0" type="warning" size="small" :bordered="false">
                    {{ t('orgSkills.pendingCount', { n: row.pending_count }) }}
                  </n-tag>
                </div>
                <p v-if="row.description" class="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                  {{ row.description }}
                </p>
                <div class="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-500 dark:text-slate-400">
                  <span>{{ t('orgSkills.latestVersion') }}: v{{ row.latest_version }}</span>
                  <span data-testid="org-skill-enabled-state">
                    {{ row.enabled_version_no === null ? t('orgSkills.notEnabled') : t('orgSkills.enabledVersion', { n: row.enabled_version_no }) }}
                  </span>
                </div>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <n-button text type="primary" size="small" :data-testid="`org-skill-open-${row.id}`" @click="openDetail(row.id)">
                  {{ t('orgSkills.open') }}
                </n-button>
              </div>
            </div>
          </div>
          <div class="mt-4 flex justify-end">
            <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
          </div>
        </div>
      </n-spin>

      <!-- 详情 / 版本 / diff / 审核 / 启用回滚归档 -->
      <n-modal
        v-model:show="detailVisible"
        preset="card"
        :title="selected?.name || t('orgSkills.detail.title')"
        class="max-w-4xl"
        data-testid="org-skill-detail-modal"
      >
        <n-spin :show="detailLoading">
          <template v-if="selected">
            <div class="mb-4 flex flex-wrap items-center gap-2">
              <n-tag :type="selected.status === 'archived' ? 'default' : 'success'" size="small">
                {{ t(`orgSkills.status.${selected.status}`) }}
              </n-tag>
              <n-tag type="info" size="small" :bordered="false">
                {{ selected.enabled_version_no === null ? t('orgSkills.notEnabled') : t('orgSkills.enabledVersion', { n: selected.enabled_version_no }) }}
              </n-tag>
              <n-tag size="small" :bordered="false">{{ t('orgSkills.latestVersion') }}: v{{ selected.latest_version }}</n-tag>
            </div>
            <p v-if="selected.description" class="mb-4 text-sm text-slate-600 dark:text-slate-300">
              {{ selected.description }}
            </p>

            <div class="mb-4 flex flex-wrap items-center gap-2">
              <n-button
                v-if="selected.enabled_version_no !== null"
                size="small"
                secondary
                :disabled="!isAdmin"
                :loading="actionBusy === 'disable'"
                data-testid="org-skill-disable"
                @click="disableSkill"
              >
                {{ t('orgSkills.detail.disable') }}
              </n-button>
              <n-button
                v-if="selected.enabled_version_no !== null"
                size="small"
                secondary
                :disabled="!isAdmin || rollbackTarget === null"
                :loading="actionBusy === 'rollback'"
                data-testid="org-skill-rollback"
                @click="rollbackSkill"
              >
                {{ t('orgSkills.detail.rollback') }}
              </n-button>
              <n-button
                v-if="selected.status === 'active'"
                size="small"
                secondary
                type="error"
                :disabled="!isAdmin"
                :loading="actionBusy === 'archive'"
                data-testid="org-skill-archive"
                @click="archiveSkill"
              >
                {{ t('orgSkills.detail.archive') }}
              </n-button>
            </div>

            <h3 class="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
              {{ t('orgSkills.detail.versions') }}
            </h3>
            <p v-if="selected.versions.length === 0" class="text-xs text-slate-500 dark:text-slate-400">
              {{ t('orgSkills.detail.noVersions') }}
            </p>
            <div v-else class="space-y-2">
              <div
                v-for="version in selected.versions"
                :key="version.id"
                class="rounded-xl border border-slate-200 p-3 dark:border-slate-700"
                :data-testid="`org-skill-version-${version.version_no}`"
              >
                <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="text-sm font-semibold text-slate-900 dark:text-slate-100">v{{ version.version_no }}</span>
                      <n-tag :type="reviewTagType(version.review_status)" size="small">
                        {{ t(`orgSkills.reviewStatus.${version.review_status}`) }}
                      </n-tag>
                      <n-tag size="small" :bordered="false">{{ t(`orgSkills.source.${version.source}`) }}</n-tag>
                      <n-tag v-if="selected.enabled_version_no === version.version_no" type="success" size="small" :bordered="false">
                        {{ t('orgSkills.detail.enabled') }}
                      </n-tag>
                    </div>
                    <div class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                      <span>{{ t('orgSkills.detail.createdAt') }}: {{ formatDate(version.created_at) }}</span>
                      <span class="ml-3">{{ t('orgSkills.detail.hash') }}: {{ version.payload_sha256.slice(0, 12) }}…</span>
                    </div>
                    <p v-if="version.review_note" class="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      {{ t('orgSkills.detail.reviewNote') }}: {{ version.review_note }}
                    </p>
                    <!-- 审核人必须看到实际会进入运行时的 payload（v1 无上一版可 diff） -->
                    <div
                      class="mt-2 flex flex-wrap items-center gap-1"
                      :data-testid="`org-skill-version-payload-${version.version_no}`"
                    >
                      <span class="text-[11px] text-slate-400 dark:text-slate-500">{{ t('orgSkills.detail.triggerPhrases') }}:</span>
                      <n-tag
                        v-for="(phrase, idx) in version.payload?.trigger_phrases || []"
                        :key="`vp-${idx}-${phrase}`"
                        size="small"
                        :bordered="false"
                      >
                        {{ phrase }}
                      </n-tag>
                      <span class="ml-2 text-[11px] text-slate-400 dark:text-slate-500">{{ t('orgSkills.detail.toolSequence') }}:</span>
                      <n-tag
                        v-for="(step, idx) in version.payload?.tool_sequence || []"
                        :key="`vs-${idx}-${step.tool_name}`"
                        type="info"
                        size="small"
                        :bordered="false"
                      >
                        {{ step.tool_name }}
                      </n-tag>
                    </div>
                    <p v-if="version.review_status === 'pending' && isAuthor(version)" class="mt-1 text-xs text-amber-600 dark:text-amber-400" data-testid="org-skill-detail-self-review-hint">
                      {{ t('orgSkills.pendingQueue.authorCannotReview') }}
                    </p>
                  </div>
                  <div class="flex shrink-0 flex-wrap items-center gap-2">
                    <template v-if="version.review_status === 'pending'">
                      <n-button
                        size="tiny"
                        type="primary"
                        :disabled="!canReview(version)"
                        :loading="actionBusy === `approve-${selected.id}-${version.version_no}`"
                        :data-testid="`org-skill-detail-approve-${version.version_no}`"
                        @click="approveVersion(selected.id, version)"
                      >
                        {{ t('orgSkills.pendingQueue.approve') }}
                      </n-button>
                      <n-button
                        size="tiny"
                        secondary
                        type="error"
                        :disabled="!canReview(version)"
                        :loading="actionBusy === `reject-${selected.id}-${version.version_no}`"
                        :data-testid="`org-skill-detail-reject-${version.version_no}`"
                        @click="rejectVersion(selected.id, version)"
                      >
                        {{ t('orgSkills.pendingQueue.reject') }}
                      </n-button>
                    </template>
                    <n-button
                      v-if="version.review_status === 'approved'"
                      size="tiny"
                      type="primary"
                      :disabled="
                        !isAdmin ||
                        selected.status !== 'active' ||
                        selected.enabled_version_no === version.version_no
                      "
                      :loading="actionBusy === `enable-${version.version_no}`"
                      :data-testid="`org-skill-enable-${version.version_no}`"
                      @click="enableVersion(version)"
                    >
                      {{ selected.enabled_version_no === version.version_no ? t('orgSkills.detail.enabled') : t('orgSkills.detail.enable') }}
                    </n-button>
                    <n-button
                      v-if="previousVersion(version)"
                      size="tiny"
                      secondary
                      :loading="diffLoading"
                      :data-testid="`org-skill-diff-${version.version_no}`"
                      @click="showDiff(version)"
                    >
                      {{ t('orgSkills.detail.comparePrevious') }}
                    </n-button>
                  </div>
                </div>
              </div>
            </div>

            <!-- 结构化 diff -->
            <div
              v-if="diff"
              class="mt-4 rounded-xl border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900/40 dark:bg-blue-950/10"
              data-testid="org-skill-diff"
            >
              <div class="mb-2 flex items-center justify-between">
                <h3 class="text-sm font-semibold text-slate-800 dark:text-slate-100">
                  {{ t('orgSkills.detail.diffTitle', { base: diff.base_version_no, target: diff.target_version_no }) }}
                </h3>
                <n-button text size="tiny" @click="diff = null">{{ t('orgSkills.detail.close') }}</n-button>
              </div>
              <div class="space-y-3 text-xs">
                <div>
                  <div class="mb-1 font-medium text-slate-700 dark:text-slate-200">{{ t('orgSkills.detail.triggerPhrases') }}</div>
                  <div class="flex flex-wrap gap-1">
                    <n-tag v-for="item in diff.trigger_phrases.added" :key="`t-add-${item}`" type="success" size="small" :bordered="false">
                      + {{ item }}
                    </n-tag>
                    <n-tag v-for="item in diff.trigger_phrases.removed" :key="`t-del-${item}`" type="error" size="small" :bordered="false">
                      - {{ item }}
                    </n-tag>
                    <span v-if="diff.trigger_phrases.added.length === 0 && diff.trigger_phrases.removed.length === 0" class="text-slate-500 dark:text-slate-400">
                      {{ t('orgSkills.detail.diffNone') }}
                    </span>
                  </div>
                </div>
                <div>
                  <div class="mb-1 font-medium text-slate-700 dark:text-slate-200">{{ t('orgSkills.detail.toolSequence') }}</div>
                  <div class="flex flex-wrap gap-1">
                    <n-tag v-for="item in diff.tool_sequence.added" :key="`s-add-${item}`" type="success" size="small" :bordered="false">
                      + {{ item }}
                    </n-tag>
                    <n-tag v-for="item in diff.tool_sequence.removed" :key="`s-del-${item}`" type="error" size="small" :bordered="false">
                      - {{ item }}
                    </n-tag>
                    <span v-if="diff.tool_sequence.added.length === 0 && diff.tool_sequence.removed.length === 0" class="text-slate-500 dark:text-slate-400">
                      {{ t('orgSkills.detail.diffNone') }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </n-spin>
      </n-modal>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { useDedupedMessage } from '@/utils/message'
import { getErrorMessage } from '@/utils/apiError'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { formatDateTime } from '@/utils/format'
import {
  orgSkillsApi,
  type OrgSkillDetail,
  type OrgSkillDiff,
  type OrgSkillListData,
  type OrgSkillSummary,
  type OrgSkillVersion,
} from '@/api/orgSkills'

const { t, locale } = useI18n()
const userStore = useUserStore()
const message = useDedupedMessage()

type StatusFilter = 'active' | 'archived' | 'all'

const loading = ref(false)
const actionBusy = ref('')
const loadError = ref('')
const statusFilter = ref<StatusFilter>('active')
const rows = ref<OrgSkillSummary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20

const selected = ref<OrgSkillDetail | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const diff = ref<OrgSkillDiff | null>(null)
const diffLoading = ref(false)

const pendingRows = ref<Array<{ skill: OrgSkillSummary; version: OrgSkillVersion }>>([])
const pendingLoading = ref(false)

const currentUserId = computed(() => userStore.userInfo?.id ?? null)
const isAdmin = computed(() => userStore.isAdmin)

const statusOptions = computed(() => [
  { label: t('orgSkills.filters.active'), value: 'active' },
  { label: t('orgSkills.filters.archived'), value: 'archived' },
  { label: t('orgSkills.filters.all'), value: 'all' },
])

const rollbackTarget = computed<number | null>(() => {
  const skill = selected.value
  if (!skill || skill.enabled_version_no === null) return null
  const candidates = skill.versions
    .filter((v) => v.review_status === 'approved' && v.version_no < (skill.enabled_version_no as number))
    .sort((a, b) => b.version_no - a.version_no)
  return candidates.length > 0 ? candidates[0].version_no : null
})

function isAuthor(version: OrgSkillVersion): boolean {
  return currentUserId.value !== null && version.created_by === currentUserId.value
}

function canReview(version: OrgSkillVersion): boolean {
  return isAdmin.value && !isAuthor(version)
}

function reviewTagType(status: OrgSkillVersion['review_status']): 'warning' | 'success' | 'error' {
  if (status === 'pending') return 'warning'
  if (status === 'approved') return 'success'
  return 'error'
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return formatDateTime(value, locale.value)
}

function previousVersion(version: OrgSkillVersion): OrgSkillVersion | null {
  if (!selected.value) return null
  const lower = selected.value.versions
    .filter((v) => v.version_no < version.version_no)
    .sort((a, b) => b.version_no - a.version_no)
  return lower.length > 0 ? lower[0] : null
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const data = apiUnwrap<OrgSkillListData>(
      await orgSkillsApi.list({ status: statusFilter.value, page: page.value, page_size: pageSize }),
    )
    rows.value = data?.items || []
    total.value = data?.total || 0
  } catch (error) {
    loadError.value = getErrorMessage(error) || t('orgSkills.loadFailed')
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
  void loadPendingQueue()
}

async function loadPendingQueue(): Promise<void> {
  pendingLoading.value = true
  try {
    const collected: Array<{ skill: OrgSkillSummary; version: OrgSkillVersion }> = []
    let pageNo = 1
    let seen = 0
    // 分页扫描全部 active 技能（后端单页上限 100）；不再按技能详情 N+1。
    for (;;) {
      const list = apiUnwrap<OrgSkillListData>(
        await orgSkillsApi.list({ status: 'active', page: pageNo, page_size: 100 }),
      )
      const items = list?.items || []
      collected.push(
        ...items.flatMap((row) =>
          (row.pending_versions || []).map((version) => ({ skill: row, version })),
        ),
      )
      seen += items.length
      const total = list?.total || 0
      if (items.length === 0 || seen >= total || pageNo >= 100) break
      pageNo += 1
    }
    pendingRows.value = collected
  } catch {
    // 待审队列是辅助视图：失败不覆盖主列表错误态。
    pendingRows.value = []
  } finally {
    pendingLoading.value = false
  }
}

function applyFilter(): void {
  page.value = 1
  void load()
}

async function openDetail(skillId: string): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  diff.value = null
  try {
    selected.value = apiUnwrap<OrgSkillDetail>(await orgSkillsApi.get(skillId))
  } catch (error) {
    message.error(getErrorMessage(error) || t('orgSkills.loadFailed'))
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (!selected.value) return
  try {
    selected.value = apiUnwrap<OrgSkillDetail>(await orgSkillsApi.get(selected.value.id))
  } catch {
    // 详情刷新失败保留旧视图；下一次动作/刷新会再试。
  }
}

async function runAction(key: string, fn: () => Promise<unknown>, successKey: string): Promise<void> {
  actionBusy.value = key
  try {
    await fn()
    message.success(t(successKey))
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    message.error(getErrorMessage(error) || t('orgSkills.actionFailed'))
  } finally {
    actionBusy.value = ''
  }
}

function approveVersion(skillId: string, version: OrgSkillVersion): void {
  void runAction(
    `approve-${skillId}-${version.version_no}`,
    () => orgSkillsApi.approve(skillId, version.version_no),
    'orgSkills.pendingQueue.approveSuccess',
  )
}

function rejectVersion(skillId: string, version: OrgSkillVersion): void {
  void runAction(
    `reject-${skillId}-${version.version_no}`,
    () => orgSkillsApi.reject(skillId, version.version_no),
    'orgSkills.pendingQueue.rejectSuccess',
  )
}
function enableVersion(version: OrgSkillVersion): void {
  if (!selected.value) return
  void runAction(
    `enable-${version.version_no}`,
    () => orgSkillsApi.enable(selected.value!.id, version.version_no),
    'orgSkills.detail.enableSuccess',
  )
}

function disableSkill(): void {
  if (!selected.value) return
  void runAction('disable', () => orgSkillsApi.disable(selected.value!.id), 'orgSkills.detail.disableSuccess')
}

function rollbackSkill(): void {
  if (!selected.value || rollbackTarget.value === null) return
  void runAction(
    'rollback',
    () => orgSkillsApi.rollback(selected.value!.id, rollbackTarget.value as number),
    'orgSkills.detail.rollbackSuccess',
  )
}

function archiveSkill(): void {
  if (!selected.value) return
  void runAction('archive', () => orgSkillsApi.archive(selected.value!.id), 'orgSkills.detail.archiveSuccess')
}

async function showDiff(version: OrgSkillVersion): Promise<void> {
  if (!selected.value) return
  const previous = previousVersion(version)
  if (!previous) return
  diffLoading.value = true
  try {
    diff.value = apiUnwrap<OrgSkillDiff>(
      await orgSkillsApi.diff(selected.value.id, previous.version_no, version.version_no),
    )
  } catch (error) {
    message.error(getErrorMessage(error) || t('orgSkills.actionFailed'))
  } finally {
    diffLoading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>
