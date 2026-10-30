<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('reviews.createCase')"
    class="max-w-2xl"
    :mask-closable="!submitting"
    :closable="!submitting"
    :close-on-esc="!submitting"
    @update:show="onShowChange"
  >
    <n-alert v-if="candidatesError" type="error" class="mb-3" :bordered="false">
      {{ t('reviews.candidatesLoadFailed') }}
      <n-button text type="primary" class="ml-2" @click="loadCandidates">{{ t('reviews.retry') }}</n-button>
    </n-alert>

    <div class="flex flex-col gap-3">
      <div>
        <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
          {{ t('reviews.createTitle') }}
        </div>
        <n-input
          v-model:value="form.title"
          :placeholder="t('reviews.createTitlePlaceholder')"
          :input-props="{ 'aria-label': t('reviews.createTitle') }"
          maxlength="255"
          data-testid="create-case-title"
        />
      </div>

      <div v-for="side in SIDES" :key="side">
        <div class="mb-1 flex items-center justify-between text-xs font-medium text-gray-500 dark:text-gray-400">
          <span>{{ sideLabel[side] }}</span>
          <n-button
            v-if="selectedCandidate[side]"
            text
            size="small"
            type="primary"
            :data-testid="`register-meta-${side}`"
            @click="openMeta(side)"
          >
            {{ selectedCandidate[side]?.meta ? t('reviews.editMeta') : t('reviews.registerMeta') }}
          </n-button>
        </div>
        <n-select
          v-model:value="sideSelection[side]"
          :options="optionsFor(side)"
          :placeholder="t('reviews.createSelectDoc')"
          :input-props="{ 'aria-label': sideLabel[side] }"
          filterable
          clearable
          :data-testid="`create-select-${side}`"
        />
        <div v-if="readiness[side]" class="mt-1 flex items-center gap-2 text-[11px]">
          <n-tag
            size="tiny"
            :type="readiness[side]?.status === 'reviewable' ? 'success' : 'error'"
          >
            {{
              readiness[side]?.status === 'reviewable'
                ? t('reviews.readinessReviewable')
                : t('reviews.readinessBlocked')
            }}
          </n-tag>
          <span
            v-if="readiness[side]?.status === 'blocked'"
            class="text-red-500 dark:text-red-400"
            :title="(readiness[side]?.blocking_reasons || []).join(' · ')"
            :data-testid="`readiness-blocked-${side}`"
          >
            {{ reasonsLabel(t, readiness[side]?.blocking_reasons) }}
          </span>
        </div>
      </div>

      <div v-if="crossDocWarning" class="text-[11px] text-amber-600 dark:text-amber-400" data-testid="cross-doc-warning">
        {{ crossDocWarning }}
      </div>
      <div v-if="anyBlocked" class="text-[11px] text-amber-600 dark:text-amber-400">
        {{ t('reviews.createBlockedHint') }}
      </div>
      <div v-if="candidateTotal > candidates.length" class="text-[11px] text-gray-400 dark:text-gray-500">
        {{ t('reviews.candidateTruncated', { total: candidateTotal, shown: candidates.length }) }}
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <n-button quaternary :disabled="submitting" @click="onShowChange(false)">
          {{ t('reviews.createCancel') }}
        </n-button>
        <n-button
          type="primary"
          :loading="submitting"
          :disabled="submitting"
          data-testid="create-case-submit"
          @click="submit"
        >
          {{ t('reviews.createSubmit') }}
        </n-button>
      </div>
    </template>
  </n-modal>

  <n-modal
    :show="metaSide !== null"
    preset="card"
    :title="t('reviews.metaDialogTitle')"
    class="max-w-md"
    :closable="!metaSaving"
    :close-on-esc="!metaSaving"
    :mask-closable="!metaSaving"
    data-testid="meta-dialog"
    @update:show="(value: boolean) => (!value ? (metaSide = null) : undefined)"
  >
    <div v-if="metaSide" class="flex flex-col gap-3">
      <div class="text-xs text-gray-500 dark:text-gray-400">{{ metaDocument?.filename }}</div>
      <div>
        <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.metaRole') }}</div>
        <n-select
          v-model:value="metaForm.doc_role"
          :options="roleOptions"
          :input-props="{ 'aria-label': t('reviews.metaRole') }"
        />
      </div>
      <div>
        <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.metaFamily') }}</div>
        <n-input
          v-model:value="metaForm.family_key"
          :input-props="{ 'aria-label': t('reviews.metaFamily') }"
          maxlength="128"
        />
      </div>
      <div>
        <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
          {{ t('reviews.metaVersionLabel') }}
        </div>
        <n-input
          v-model:value="metaForm.version_label"
          :input-props="{ 'aria-label': t('reviews.metaVersionLabel') }"
          maxlength="64"
        />
      </div>
      <div>
        <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.metaVersionSeq') }}</div>
        <n-input-number
          v-model:value="metaForm.version_seq"
          :min="1"
          :max="2147483647"
          :precision="0"
          :input-props="{ 'aria-label': t('reviews.metaVersionSeq') }"
          class="w-full"
        />
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <n-button quaternary :disabled="metaSaving" @click="metaSide = null">
          {{ t('reviews.createCancel') }}
        </n-button>
        <n-button
          type="primary"
          :loading="metaSaving"
          :disabled="metaSaving"
          data-testid="meta-submit"
          @click="saveMeta"
        >
          {{ t('reviews.metaSubmit') }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NInput, NInputNumber, NModal, NSelect, NTag, useMessage } from 'naive-ui'
import {
  reviewsApi,
  type ReviewCandidate,
  type ReviewMetaPayload,
  type ReviewReadiness,
  type ReviewSide
} from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { reasonsLabel } from '../reviewReasons'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'created', caseId: string): void
}>()

const { t } = useI18n()
const message = useMessage()

const SIDES: ReviewSide[] = ['policy', 'base', 'revised']

const form = reactive({ title: '' })
const sideSelection = reactive<Record<ReviewSide, string | null>>({
  policy: null,
  base: null,
  revised: null
})
const candidates = ref<ReviewCandidate[]>([])
const candidateTotal = ref(0)
const candidatesError = ref(false)
const readiness = reactive<Partial<Record<ReviewSide, ReviewReadiness>>>({})
const submitting = ref(false)

const metaSide = ref<ReviewSide | null>(null)
const metaSaving = ref(false)
const metaForm = reactive<{
  doc_role: 'policy' | 'template' | 'contract'
  family_key: string
  version_label: string
  version_seq: number | null
}>({ doc_role: 'contract', family_key: '', version_label: '', version_seq: null })

const sideLabel = computed<Record<ReviewSide, string>>(() => ({
  policy: t('reviews.docPolicy'),
  base: t('reviews.docBase'),
  revised: t('reviews.docRevised')
}))

// 角色按侧锁定（首次登记）：制度侧只能 policy/template，合同侧只能 contract，
// 防止把合同误登记成制度后从另一侧消失。编辑已有身份时放开全部角色——
// 「选错侧先登记」或历史错误角色需要一条 UI 内的修复路径（upsert 覆盖）。
const ALL_ROLE_OPTIONS = computed(() => [
  { value: 'policy', label: t('reviews.metaRolePolicy') },
  { value: 'template', label: t('reviews.metaRoleTemplate') },
  { value: 'contract', label: t('reviews.metaRoleContract') }
])

const editingExistingMeta = computed(() => {
  const side = metaSide.value
  return !!(side && selectedCandidate.value[side]?.meta)
})

const roleOptions = computed(() => {
  if (editingExistingMeta.value) return ALL_ROLE_OPTIONS.value
  if (metaSide.value === 'policy') {
    return [
      { value: 'policy', label: t('reviews.metaRolePolicy') },
      { value: 'template', label: t('reviews.metaRoleTemplate') }
    ]
  }
  return [{ value: 'contract', label: t('reviews.metaRoleContract') }]
})

function errorText(error: unknown, fallback: string) {
  const text = getErrorMessage(error, fallback)
  // 401 续期失败会抛内部标记（refresh_token_missing / refresh_access_token_missing），
  // 不得作为英文串上屏。
  const internalMarkers = new Set(['refresh_token_missing', 'refresh_access_token_missing'])
  return internalMarkers.has(text) ? fallback : text
}

function roleText(role: string) {
  if (role === 'policy') return t('reviews.metaRolePolicy')
  if (role === 'template') return t('reviews.metaRoleTemplate')
  if (role === 'contract') return t('reviews.metaRoleContract')
  return role
}

function optionLabel(candidate: ReviewCandidate) {
  const meta = candidate.meta
  if (!meta) return `${candidate.filename} · ${t('reviews.candidateNoMeta')}`
  const parts = [roleText(String(meta.doc_role))]
  if (meta.version_label) parts.push(String(meta.version_label))
  if (meta.family_key) parts.push(`(${meta.family_key})`)
  return `${candidate.filename} · ${parts.join(' ')}`
}

function optionsFor(side: ReviewSide) {
  const wanted = side === 'policy' ? ['policy', 'template'] : ['contract']
  const taken = new Set(
    SIDES.filter((other) => other !== side)
      .map((other) => sideSelection[other])
      .filter((id): id is string => !!id)
  )
  return candidates.value
    .filter((candidate) => !taken.has(candidate.document_id))
    .filter((candidate) => !candidate.meta || wanted.includes(String(candidate.meta.doc_role)))
    .map((candidate) => ({ value: candidate.document_id, label: optionLabel(candidate) }))
}

const selectedCandidate = computed<Record<ReviewSide, ReviewCandidate | null>>(() => {
  const byId = new Map(candidates.value.map((candidate) => [candidate.document_id, candidate]))
  return {
    policy: sideSelection.policy ? byId.get(sideSelection.policy) ?? null : null,
    base: sideSelection.base ? byId.get(sideSelection.base) ?? null : null,
    revised: sideSelection.revised ? byId.get(sideSelection.revised) ?? null : null
  }
})

const anyBlocked = computed(() =>
  SIDES.some((side) => readiness[side]?.status === 'blocked')
)

// 后端建案时的两条跨文档约束（family_mismatch / version_order）用候选 meta 镜像预检，
// 避免「两侧全绿、建案即阻断」。
const crossDocWarning = computed(() => {
  const base = selectedCandidate.value.base?.meta
  const revised = selectedCandidate.value.revised?.meta
  if (!base || !revised) return ''
  if (String(base.doc_role) !== 'contract' || String(revised.doc_role) !== 'contract') return ''
  // 与后端 _collect_blocking_reasons 逐字同口径：先比较 family（任一侧为空也算不一致），
  // 再比较版本序（base.version_seq >= revised.version_seq → version_order）。
  const baseFamily = String(base.family_key ?? '').trim()
  const revisedFamily = String(revised.family_key ?? '').trim()
  if (baseFamily !== revisedFamily) return t('reviews.warnFamilyMismatch')
  if (
    base.version_seq != null &&
    revised.version_seq != null &&
    base.version_seq >= revised.version_seq
  ) {
    return t('reviews.warnVersionOrder')
  }
  return ''
})

async function loadCandidates() {
  candidatesError.value = false
  try {
    const data = apiUnwrap<{ items: ReviewCandidate[]; total?: number }>(
      await reviewsApi.candidates({ page: 1, page_size: 200 })
    )
    candidates.value = data?.items ?? []
    candidateTotal.value = data?.total ?? candidates.value.length
  } catch {
    candidatesError.value = true
  }
  // 身份登记/刷新后，已选文档可能不再匹配该侧角色（如合同被登记成制度）。
  for (const side of SIDES) {
    const id = sideSelection[side]
    if (!id) continue
    const candidate = candidates.value.find((item) => item.document_id === id)
    const wanted = side === 'policy' ? ['policy', 'template'] : ['contract']
    if (!candidate || (candidate.meta && !wanted.includes(String(candidate.meta.doc_role)))) {
      sideSelection[side] = null
    }
  }
  await refreshReadiness()
}

let readinessToken = 0

async function refreshReadiness() {
  const token = ++readinessToken
  for (const side of SIDES) delete readiness[side]
  const ids = SIDES.map((side) => sideSelection[side])
  if (ids.some((id) => !id)) return
  await Promise.all(
    SIDES.map(async (side) => {
      const id = sideSelection[side]
      if (!id) return
      try {
        const result = apiUnwrap<ReviewReadiness>(await reviewsApi.readiness(id))
        // 快速切换选择时只认最后一次请求的结果，避免旧响应覆盖新选择。
        if (token === readinessToken) readiness[side] = result
      } catch {
        // 可用性只是建案前的提示；失败不阻塞建案，详情页仍有 readiness 快照。
      }
    })
  )
}

watch(
  () => [sideSelection.policy, sideSelection.base, sideSelection.revised],
  () => {
    void refreshReadiness()
  }
)

watch(
  () => props.show,
  (value) => {
    if (value) {
      form.title = ''
      sideSelection.policy = null
      sideSelection.base = null
      sideSelection.revised = null
      void loadCandidates()
    }
  }
)

function onShowChange(value: boolean) {
  emit('update:show', value)
}

function openMeta(side: ReviewSide) {
  const candidate = selectedCandidate.value[side]
  if (!candidate) return
  metaSide.value = side
  const known: Array<'policy' | 'template' | 'contract'> = ['policy', 'template', 'contract']
  const existing = candidate.meta ? (String(candidate.meta.doc_role) as 'policy' | 'template' | 'contract') : null
  const sideDefault: 'policy' | 'contract' = side === 'policy' ? 'policy' : 'contract'
  metaForm.doc_role = existing && known.includes(existing) ? existing : sideDefault
  metaForm.family_key = candidate.meta?.family_key || ''
  metaForm.version_label = candidate.meta?.version_label || ''
  metaForm.version_seq = candidate.meta?.version_seq ?? null
}

const metaDocument = computed(() =>
  metaSide.value ? selectedCandidate.value[metaSide.value] : null
)

async function saveMeta() {
  if (!metaSide.value || !metaDocument.value) return
  const payload: ReviewMetaPayload = { doc_role: metaForm.doc_role }
  if (metaForm.doc_role === 'contract') {
    if (!metaForm.family_key.trim() || !metaForm.version_label.trim() || !metaForm.version_seq) {
      message.warning(t('reviews.metaRequired'))
      return
    }
    payload.family_key = metaForm.family_key.trim()
    payload.version_label = metaForm.version_label.trim()
    payload.version_seq = metaForm.version_seq
  } else {
    if (metaForm.family_key.trim()) payload.family_key = metaForm.family_key.trim()
    if (metaForm.version_label.trim()) payload.version_label = metaForm.version_label.trim()
    if (metaForm.version_seq != null) payload.version_seq = metaForm.version_seq
    if (payload.version_seq != null && !payload.version_label) {
      message.warning(t('reviews.metaSeqNeedsLabel'))
      return
    }
  }
  metaSaving.value = true
  try {
    await reviewsApi.putDocumentMeta(metaDocument.value.document_id, payload)
    message.success(t('reviews.metaSaved'))
    metaSide.value = null
    await loadCandidates()
  } catch (error) {
    message.error(errorText(error, t('reviews.metaFailed')))
  } finally {
    metaSaving.value = false
  }
}

async function submit() {
  if (submitting.value) return
  if (!form.title.trim()) {
    message.warning(t('reviews.createTitleRequired'))
    return
  }
  const ids = SIDES.map((side) => sideSelection[side])
  if (ids.some((id) => !id)) {
    message.warning(t('reviews.createDocsRequired'))
    return
  }
  if (new Set(ids).size !== ids.length) {
    message.warning(t('reviews.createDocsDistinct'))
    return
  }
  submitting.value = true
  try {
    const created = apiUnwrap<{ id: string }>(
      await reviewsApi.createCase({
        title: form.title.trim(),
        policy_document_id: ids[0]!,
        base_document_id: ids[1]!,
        revised_document_id: ids[2]!,
        checklist_version: 'v1'
      })
    )
    emit('created', created.id)
    emit('update:show', false)
  } catch (error) {
    message.error(errorText(error, t('reviews.createFailed')))
  } finally {
    submitting.value = false
  }
}

defineExpose({
  form,
  sideSelection,
  candidates,
  candidateTotal,
  candidatesError,
  readiness,
  selectedCandidate,
  crossDocWarning,
  roleOptions,
  optionLabel,
  optionsFor,
  submit,
  saveMeta,
  openMeta,
  loadCandidates,
  refreshReadiness
})
</script>
