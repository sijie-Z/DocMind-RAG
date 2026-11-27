<template>
  <!-- 公开知识库只读检索页（批次十三）：无登录、无生成、无内部字段。
       token 即全部权限；撤销/过期/禁用/不存在统一显示「不可用」。 -->
  <div class="public-kb-page">
    <header class="public-kb-page-header">
      <div class="public-kb-brand">DocMind</div>
      <div class="public-kb-heading">
        <h1 class="public-kb-page-title">
          {{ meta?.title || t('publicKb.public.title') }}
        </h1>
        <p v-if="meta?.description" class="public-kb-page-desc" data-testid="public-kb-description">
          {{ meta.description }}
        </p>
        <p v-if="meta?.expires_at" class="public-kb-page-expiry" data-testid="public-kb-expires-at">
          {{ t('publicKb.expiresAt', { time: formatTime(meta.expires_at) }) }}
        </p>
      </div>
    </header>

    <main class="public-kb-page-main">
      <div v-if="loading" class="public-kb-page-state" role="status" data-testid="public-kb-loading">
        {{ t('publicKb.loading') }}
      </div>
      <div
        v-else-if="loadError"
        class="public-kb-page-error"
        role="alert"
        data-testid="public-kb-load-error"
      >
        <span>{{ t('publicKb.public.loadFailed') }}</span>
        <button class="public-kb-retry" data-testid="public-kb-retry" @click="retryLoad">
          {{ t('publicKb.retry') }}
        </button>
      </div>
      <div
        v-else-if="unavailable"
        class="public-kb-page-error"
        role="alert"
        data-testid="public-kb-unavailable"
      >
        {{ t('publicKb.public.unavailable') }}
      </div>
      <template v-else>
        <form class="public-kb-search" @submit.prevent="runSearch">
          <input
            v-model="query"
            class="public-kb-search-input"
            data-testid="public-kb-search-input"
            :placeholder="t('publicKb.public.searchPlaceholder')"
            maxlength="200"
            :aria-label="t('publicKb.public.searchPlaceholder')"
          />
          <button
            class="public-kb-search-btn"
            type="submit"
            data-testid="public-kb-search-submit"
            :disabled="searching"
          >
            {{ searching ? t('publicKb.public.searching') : t('publicKb.public.search') }}
          </button>
        </form>

        <p v-if="searchError" class="public-kb-page-error" role="alert" data-testid="public-kb-search-error">
          {{ t('publicKb.public.loadFailed') }}
        </p>

        <template v-else-if="searched">
          <p class="public-kb-result-meta" data-testid="public-kb-result-total">
            {{ t('publicKb.public.resultCount', { n: results.length }) }}
          </p>
          <div
            v-if="results.length === 0"
            class="public-kb-page-state"
            data-testid="public-kb-empty"
          >
            <p>{{ t('publicKb.public.empty') }}</p>
            <p class="public-kb-result-hint">{{ t('publicKb.public.emptyHint') }}</p>
          </div>
          <ul v-else class="public-kb-results" data-testid="public-kb-results">
            <li
              v-for="item in results"
              :key="item.rank"
              class="public-kb-result"
              :data-testid="`public-kb-result-${item.rank}`"
            >
              <div class="public-kb-result-head">
                <span class="public-kb-result-rank">#{{ item.rank }}</span>
                <span class="public-kb-result-title">{{ item.title }}</span>
                <span class="public-kb-result-score">{{ item.score.toFixed(3) }}</span>
              </div>
              <p class="public-kb-result-snippet">{{ item.snippet }}</p>
            </li>
          </ul>
        </template>
      </template>
    </main>

    <footer class="public-kb-page-footer">{{ t('publicKb.public.poweredBy') }}</footer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  publicKbPublicApi,
  type PublicKbMeta,
  type PublicKbSearchHit,
} from '@/api/publicKb'

const route = useRoute()
const { t } = useI18n()

const meta = ref<PublicKbMeta | null>(null)
const loading = ref(true)
const unavailable = ref(false)
const loadError = ref(false)
const query = ref('')
const searching = ref(false)
const searched = ref(false)
const results = ref<PublicKbSearchHit[]>([])
const searchError = ref(false)

/** 只有 404 才代表「链接不存在/失效」；502/429/网络错误必须是可重试错误态。 */
function isNotFound(error: unknown): boolean {
  const candidate = error as { response?: { status?: number }; status?: number }
  const status = candidate?.response?.status ?? candidate?.status
  return status === 404
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function load(token: string) {
  loading.value = true
  unavailable.value = false
  loadError.value = false
  meta.value = null
  searched.value = false
  results.value = []
  searchError.value = false
  try {
    meta.value = await publicKbPublicApi.get(token)
  } catch (error) {
    if (isNotFound(error)) unavailable.value = true
    else loadError.value = true
  } finally {
    loading.value = false
  }
}

function retryLoad() {
  const token = String(route.params.token || '')
  if (token) void load(token)
}

async function runSearch() {
  const token = String(route.params.token || '')
  if (!token || searching.value) return
  const text = query.value.trim()
  if (!text) {
    searched.value = true
    results.value = []
    return
  }
  searching.value = true
  searchError.value = false
  try {
    const data = await publicKbPublicApi.search(token, text, 10)
    results.value = data.results
    searched.value = true
  } catch (error) {
    if (isNotFound(error)) {
      // 会话期间链接被撤销/过期：与「不可用」同形，不区分原因。
      unavailable.value = true
    } else {
      // 502/429/网络故障：保留搜索框，显示可重试错误，绝不谎称链接已失效。
      searchError.value = true
    }
  } finally {
    searching.value = false
  }
}

// SPA 内从一个公开链接导航到另一个时重新拉取（组件复用）。
watch(
  () => String(route.params.token || ''),
  (token) => {
    if (token) void load(token)
  },
  { immediate: true },
)

onMounted(() => {
  if (!route.params.token) loading.value = false
})
</script>

<style scoped>
.public-kb-page {
  min-height: 100vh;
  background: #f8fafc;
  color: #0f172a;
  display: flex;
  flex-direction: column;
}
.dark .public-kb-page {
  background: #020617;
  color: #e2e8f0;
}
.public-kb-page-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px 20px;
  border-bottom: 1px solid #e2e8f0;
  background: #fff;
}
.dark .public-kb-page-header {
  border-color: #1e293b;
  background: #0f172a;
}
.public-kb-brand {
  font-weight: 800;
  letter-spacing: 0.02em;
  color: #1d4ed8;
  white-space: nowrap;
}
.dark .public-kb-brand {
  color: #93c5fd;
}
.public-kb-page-title {
  font-size: 22px;
  font-weight: 800;
}
.public-kb-page-desc {
  margin-top: 4px;
  font-size: 14px;
  color: #475569;
  white-space: pre-wrap;
}
.dark .public-kb-page-desc {
  color: #94a3b8;
}
.public-kb-page-expiry {
  margin-top: 4px;
  font-size: 12px;
  color: #b45309;
}
.public-kb-page-main {
  width: min(880px, 100%);
  margin: 0 auto;
  padding: 24px 20px 40px;
  flex: 1;
}
.public-kb-page-state {
  padding: 18px 0;
  color: #64748b;
  font-size: 14px;
}
.public-kb-page-error {
  padding: 16px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 12px;
  font-size: 14px;
}
.public-kb-retry {
  margin-left: 12px;
  border: 1px solid #b91c1c;
  border-radius: 8px;
  background: transparent;
  color: #b91c1c;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.dark .public-kb-retry {
  border-color: #fca5a5;
  color: #fca5a5;
}
.dark .public-kb-page-error {
  border-color: #7f1d1d;
  background: #450a0a;
  color: #fca5a5;
}
.public-kb-search {
  display: flex;
  gap: 10px;
}
.public-kb-search-input {
  flex: 1;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
  background: #fff;
  color: #0f172a;
}
.dark .public-kb-search-input {
  border-color: #334155;
  background: #0f172a;
  color: #e2e8f0;
}
.public-kb-search-btn {
  border: 1px solid #2563eb;
  border-radius: 10px;
  background: #2563eb;
  color: #fff;
  padding: 10px 18px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.public-kb-search-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.public-kb-result-meta {
  margin: 18px 0 8px;
  font-size: 13px;
  color: #64748b;
}
.public-kb-result-hint {
  margin-top: 4px;
  font-size: 12px;
}
.public-kb-results {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.public-kb-result {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  padding: 14px 16px;
}
.dark .public-kb-result {
  border-color: #1e293b;
  background: #0f172a;
}
.public-kb-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.public-kb-result-rank {
  font-size: 12px;
  font-weight: 700;
  color: #2563eb;
}
.dark .public-kb-result-rank {
  color: #93c5fd;
}
.public-kb-result-title {
  font-weight: 700;
  font-size: 15px;
}
.public-kb-result-score {
  margin-left: auto;
  font-size: 12px;
  color: #64748b;
  font-variant-numeric: tabular-nums;
}
.public-kb-result-snippet {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
}
.dark .public-kb-result-snippet {
  color: #cbd5e1;
}
.public-kb-page-footer {
  border-top: 1px solid #e2e8f0;
  padding: 14px 20px;
  text-align: center;
  font-size: 12px;
  color: #64748b;
}
.dark .public-kb-page-footer {
  border-color: #1e293b;
}
</style>
