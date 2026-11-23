<template>
  <div class="search-page h-full flex flex-col p-4 md:p-6 space-y-5 overflow-hidden bg-transparent">
    <!-- 顶部标题区域 -->
    <div class="search-header flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
          {{ t('menu.search') }}
        </h1>
        <p class="mt-1 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
          <span class="h-0.5 w-6 rounded-full bg-blue-600"></span>
          {{ t('search.subtitle') }}
        </p>
      </div>
      <div v-if="searchStats" class="search-stats flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
        <span>{{ t('search.totalFiles') }}</span>
        <span class="font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ searchStats.total_files }}</span>
      </div>
    </div>

    <!-- 核心搜索框 -->
    <div class="relative max-w-4xl mx-auto w-full">
      <div
        class="flex items-stretch gap-2 rounded-xl border border-slate-200 bg-white p-2 transition-colors focus-within:border-blue-600 focus-within:ring-2 focus-within:ring-blue-600/20 dark:border-slate-700 dark:bg-slate-900"
      >
        <n-icon size="22" class="ml-2 self-center text-slate-400"><search-outline /></n-icon>
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="t('search.placeholder')"
          :aria-label="t('search.placeholder')"
          class="min-w-0 flex-1 border-none bg-transparent px-1 text-base text-slate-800 outline-none placeholder:text-slate-500 dark:text-slate-100 dark:placeholder:text-slate-400"
          @keyup.enter="handleSearch"
          @input="handleInputChange"
          @blur="hideSuggestions"
          @keydown.esc="suggestions = []"
        />
        <button
          class="search-submit inline-flex min-h-11 items-center gap-2 rounded-lg bg-blue-600 px-5 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!searchQuery.trim() || loading"
          @click="handleSearch"
        >
          <n-spin v-if="loading" size="small" stroke="white" />
          <span>{{ t('search.button') }}</span>
        </button>

        <!-- 搜索建议浮窗 -->
        <div
          v-if="suggestions.length > 0"
          class="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <div
            v-for="suggestion in suggestions"
            :key="suggestion"
            role="button"
            tabindex="0"
            class="suggestion-item flex cursor-pointer items-center gap-3 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
            @mousedown.prevent
            @click="selectSuggestion(suggestion)"
            @keydown.enter.prevent="selectSuggestion(suggestion)"
            @keydown.space.prevent="selectSuggestion(suggestion)"
          >
            <n-icon class="text-slate-400"><time-outline /></n-icon>
            <span>{{ suggestion }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选与结果主区域 -->
    <div class="search-layout flex flex-1 flex-col gap-5 overflow-hidden lg:flex-row">
      <!-- 左侧筛选栏 -->
      <aside class="search-filter w-full flex flex-col gap-4 overflow-y-auto pr-1 custom-scrollbar lg:w-72">
        <div class="space-y-5 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
          <!-- 搜索模式 -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.type') }}</h3>
            <n-radio-group v-model:value="searchType" name="searchType" class="w-full">
              <n-space vertical>
                <n-radio value="auto">{{ t('search.auto') }}</n-radio>
                <n-radio value="hybrid">{{ t('search.hybrid') }}</n-radio>
                <n-radio value="vector">{{ t('search.semantic') }}</n-radio>
                <n-radio value="keyword">{{ t('search.keyword') }}</n-radio>
              </n-space>
            </n-radio-group>
          </div>

          <!-- 知识库范围（空 KB = 空结果，不回退组织全量） -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              {{ t('search.knowledgeBase') }}
            </h3>
            <n-select
              v-model:value="selectedKnowledgeBaseId"
              clearable
              filterable
              :options="knowledgeBaseOptions"
              :loading="knowledgeBaseLoading"
              :placeholder="t('search.knowledgeBaseHint')"
              :max-tag-count="1"
              data-testid="search-knowledge-base"
            />
            <p v-if="knowledgeBaseLoadFailed" class="text-xs text-amber-700 dark:text-amber-400">
              {{ t('search.knowledgeBaseLoadFailed') }}
            </p>
          </div>

          <!-- 文档范围（检索自测） -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              {{ t('search.documentScope') }}
            </h3>
            <n-select
              v-model:value="selectedDocumentIds"
              multiple
              filterable
              remote
              clearable
              :options="documentOptions"
              :loading="documentLoading"
              :placeholder="t('search.documentScopeHint')"
              :max-tag-count="2"
              data-testid="search-document-scope"
              @search="loadDocumentOptions"
            />
          </div>

          <!-- 标签（文档 keywords 精确匹配，多选 OR） -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              {{ t('search.tags') }}
            </h3>
            <n-select
              v-model:value="selectedTags"
              multiple
              filterable
              clearable
              tag
              :options="tagOptions"
              :placeholder="t('search.tagsHint')"
              :max-tag-count="2"
              data-testid="search-tags"
            />
          </div>

          <!-- 文件类型（后端 DocumentType 枚举） -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              {{ t('search.fileTypes') }}
            </h3>
            <n-select
              v-model:value="selectedFileTypes"
              multiple
              clearable
              :options="fileTypeOptions"
              :placeholder="t('search.fileTypeHint')"
              :max-tag-count="2"
              data-testid="search-file-types"
            />
          </div>

          <!-- 返回条数 -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.topK') }}</h3>
            <n-select
              v-model:value="topK"
              :options="topKOptions"
              clearable
              :placeholder="t('search.topKDefault')"
            />
          </div>

          <!-- 排序方式 -->
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.sort') }}</h3>
            <n-select v-model:value="sortBy" :options="sortOptions" />
          </div>

          <!-- 诊断开关 -->
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.debug') }}</h3>
              <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{{ t('search.debugHint') }}</p>
            </div>
            <n-switch v-model:value="debugEnabled" data-testid="search-debug-switch" />
          </div>

          <!-- 重置按钮 -->
          <n-button block quaternary data-testid="search-clear-filters" @click="clearFilters">
            {{ t('search.clear') }}
          </n-button>
        </div>
      </aside>

      <!-- 右侧结果列表 -->
      <section class="search-results flex flex-1 flex-col overflow-hidden">
        <!-- 结果状态栏 -->
        <div v-if="searchResults.length > 0" class="mb-3 flex flex-wrap items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
          <span>{{ t('search.results') }}: <span class="font-semibold tabular-nums text-blue-700 dark:text-blue-400">{{ searchResults.length }}</span></span>
          <span v-if="lastResponse?.search_mode" data-testid="search-effective-mode">{{ searchModeLabel }}</span>
          <span v-if="lastResponse?.search_time != null">· {{ lastResponse.search_time }}s</span>
          <n-button
            size="small"
            secondary
            round
            class="ml-auto"
            data-testid="search-capture-eval-case"
            @click="openCaptureModal"
          >
            <template #icon><n-icon><SaveOutline /></n-icon></template>
            {{ t('search.captureAsEvalCase') }}
          </n-button>
        </div>

        <div class="search-results-scroll flex-1 space-y-4 overflow-y-auto pr-2 custom-scrollbar">
          <!-- 结果列表 -->
          <div v-if="searchResults.length > 0" class="space-y-3">
            <div
              v-for="(result, index) in sortedResults"
              :key="result.chunk_id || `${result.id}-${index}`"
              role="button"
              tabindex="0"
              :aria-label="filenameOf(result)"
              class="group cursor-pointer rounded-lg border border-slate-200 bg-white p-4 transition-colors hover:border-blue-300 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/50"
              :data-testid="`search-result-${index}`"
              @click="showResultDetail(result)"
              @keydown.enter="handleCardActivate($event, result)"
              @keydown.space="handleCardActivate($event, result)"
            >
              <div class="mb-2 flex items-start justify-between gap-3">
                <div class="flex min-w-0 items-start gap-3">
                  <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-50 dark:bg-slate-800">
                    <n-icon size="22" class="text-slate-500 dark:text-slate-400">
                      <component :is="getFileTypeIcon(fileTypeOf(result))" />
                    </n-icon>
                  </div>
                  <span
                    class="mt-0.5 inline-flex h-6 min-w-6 items-center justify-center rounded-md bg-slate-100 px-1 text-xs font-semibold tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                    :title="t('search.rankLabel')"
                  >#{{ result.rank ?? index + 1 }}</span>
                  <div class="min-w-0">
                    <h3 class="truncate font-semibold text-slate-800 transition-colors group-hover:text-blue-700 dark:text-slate-100 dark:group-hover:text-blue-400">
                      {{ filenameOf(result) || t('search.unknownFile') }}
                    </h3>
                    <div class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                      <n-tag v-if="rerankScoreOf(result)" :bordered="false" size="small" round type="success">
                        {{ t('search.rerankScore', { score: rerankScoreOf(result) }) }}
                      </n-tag>
                      <n-tag v-if="result.page_number" :bordered="false" size="small" round>
                        {{ t('search.pageLabel') }} {{ result.page_number }}
                      </n-tag>
                      <n-tag v-if="sectionLabelOf(result)" :bordered="false" size="small" round class="max-w-[12rem] truncate" :title="sectionLabelOf(result)">
                        {{ sectionLabelOf(result) }}
                      </n-tag>
                      <n-tag v-if="result.has_keyword" :bordered="false" size="small" round>
                        {{ t('search.hitKeyword') }}
                      </n-tag>
                      <n-tag v-if="result.has_vector" :bordered="false" size="small" round>
                        {{ t('search.hitVector') }}
                      </n-tag>
                    </div>
                  </div>
                </div>
              </div>

              <div class="text-sm leading-relaxed text-slate-600 line-clamp-2 dark:text-slate-300">
                {{ contentOf(result) }}
              </div>
              <div
                v-if="isUnverifiedText(result)"
                data-testid="search-provenance-warning"
                class="mt-1 text-xs text-amber-700 dark:text-amber-400"
              >
                {{ t('search.legacyTextWarning') }}
              </div>
              <div
                v-if="isManualOverride(result)"
                data-testid="search-manual-override-badge"
                class="mt-1 text-xs text-slate-600 dark:text-slate-300"
              >
                {{ t('search.manualOverrideWarning') }}
              </div>

              <div class="mt-3 flex flex-wrap items-center justify-between gap-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                <div class="flex min-w-0 flex-wrap items-center gap-2">
                  <n-button size="tiny" secondary round type="primary" class="result-action" @click.stop="askAI(result)">
                    <template #icon><n-icon><ChatbubblesOutline /></n-icon></template>
                    {{ t('search.askAI') }}
                  </n-button>
                  <n-button
                    size="tiny"
                    secondary
                    round
                    class="result-action"
                    data-testid="search-open-original"
                    @click.stop="openOriginal(result)"
                  >
                    <template #icon><n-icon><DocumentTextOutline /></n-icon></template>
                    {{ t('search.openOriginal') }}
                  </n-button>
                </div>
                <n-button
                  size="tiny"
                  quaternary
                  circle
                  class="result-action shrink-0 ml-auto"
                  data-testid="search-result-copy"
                  :aria-label="t('common.copy')"
                  :title="t('common.copy')"
                  @click.stop="copyResultContent(result)"
                >
                  <template #icon><n-icon><CopyOutline /></n-icon></template>
                </n-button>
              </div>
            </div>
          </div>

          <!-- 失败态（E14/D2） -->
          <div v-else-if="searchError && !loading" class="search-error flex h-full flex-col items-center justify-center p-12 text-center">
            <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-900/20">
              <n-icon size="30" class="text-red-600"><AlertCircleOutline /></n-icon>
            </div>
            <h3 class="mb-2 text-lg font-semibold text-slate-800 dark:text-slate-100">{{ t('search.searchFailed') }}</h3>
            <p class="search-error-msg mb-6 max-w-md text-sm text-slate-500 dark:text-slate-400">{{ searchError }}</p>
            <n-button v-if="currentOrgId" class="search-retry" type="primary" round @click="handleSearch">
              <template #icon><n-icon><RefreshOutline /></n-icon></template>
              {{ t('common.retry') }}
            </n-button>
          </div>

          <!-- 无结果诊断 -->
          <div v-else-if="hasSearched && !loading" class="flex h-full flex-col items-center justify-center p-8 text-center" data-testid="search-empty-state">
            <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
              <n-icon size="30" class="text-slate-400"><SearchOutline /></n-icon>
            </div>
            <h3 class="mb-2 text-lg font-semibold text-slate-800 dark:text-slate-100">{{ emptyState.title }}</h3>
            <p class="max-w-md text-sm text-slate-500 dark:text-slate-400">{{ emptyState.desc }}</p>
            <ul v-if="emptyState.scope?.length" class="mt-4 w-full max-w-md space-y-1 text-left text-xs">
              <li
                v-for="item in emptyState.scope"
                :key="item.document_id"
                class="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
              >
                <span class="truncate text-slate-600 dark:text-slate-300">{{ item.title || item.document_id.slice(0, 8) }}</span>
                <span :class="item.found && item.status === 'indexed' ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'">
                  {{ scopeStatusLabel(item) }}
                </span>
              </li>
            </ul>
          </div>

          <!-- 初始状态 -->
          <div v-else-if="!hasSearched && !loading" class="flex h-full flex-col items-center justify-center p-12 text-center">
            <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
              <n-icon size="30" class="text-slate-400"><SparklesOutline /></n-icon>
            </div>
            <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('search.startHint') }}</p>
          </div>
        </div>

        <!-- 检索诊断（复用聊天调试面板：每臂候选/RRF/rerank/MMR） -->
        <DebugPanel
          v-if="debugEnabled && lastResponse?.debug"
          :debug-data="lastResponse.debug as unknown as RetrievalDebugData"
          :default-expanded="false"
          @close="debugEnabled = false"
        />
      </section>
    </div>

    <!-- 结果详情模态框 -->
    <n-modal
      v-model:show="showDetailModal"
      preset="card"
      class="search-result-detail-modal"
      style="width: min(800px, 90vw)"
      :title="selectedResult ? filenameOf(selectedResult) : ''"
    >
      <div v-if="selectedResult" class="space-y-5 p-1">
        <div class="grid grid-cols-2 gap-3 md:grid-cols-3">
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.fileType') }}</span>
            <span class="font-semibold text-slate-800 dark:text-slate-100">{{ fileTypeOf(selectedResult).toUpperCase() || '—' }}</span>
          </div>
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.rerankScoreLabel') }}</span>
            <span class="font-semibold text-emerald-700 dark:text-emerald-400">{{ rerankScoreOf(selectedResult) ?? '—' }}</span>
          </div>
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.rankLabel') }}</span>
            <span class="font-semibold tabular-nums text-slate-800 dark:text-slate-100">#{{ selectedResult.rank ?? '—' }}</span>
          </div>
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.pageLabel') }}</span>
            <span class="font-semibold text-slate-800 dark:text-slate-100">{{ selectedResult.page_number ?? '—' }}</span>
          </div>
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.chunkLabel') }}</span>
            <span class="block truncate font-semibold text-slate-800 dark:text-slate-100" :title="selectedResult.chunk_id ?? ''">{{ selectedResult.chunk_id ?? '—' }}</span>
          </div>
          <div class="min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <span class="mb-1 block text-xs text-slate-500 dark:text-slate-400">{{ t('search.sectionLabel') }}</span>
            <span class="block truncate font-semibold text-slate-800 dark:text-slate-100" :title="sectionLabelOf(selectedResult)">{{ sectionLabelOf(selectedResult) || '—' }}</span>
          </div>
        </div>

        <div class="space-y-2">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.fileContent') }}</h4>
          <div class="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed text-slate-700 custom-scrollbar dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
            {{ contentOf(selectedResult) }}
          </div>
          <div
            v-if="isUnverifiedText(selectedResult)"
            data-testid="search-modal-provenance-warning"
            class="text-xs text-amber-700 dark:text-amber-400"
          >
            {{ t('search.legacyTextWarning') }}
          </div>
        </div>

        <div class="flex justify-end gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
          <n-button round secondary data-testid="search-modal-copy" @click="copyResultContent(selectedResult)">
            <template #icon><n-icon><CopyOutline /></n-icon></template>
            {{ t('common.copy') }}
          </n-button>
          <n-button type="primary" round @click="askAI(selectedResult)">
            <template #icon><n-icon><ChatbubblesOutline /></n-icon></template>
            {{ t('search.askAI') }}
          </n-button>
        </div>
      </div>
    </n-modal>

    <!-- 保存为评测用例（bad-case → eval case）：把本次 query 与期望文档存成评测用例 -->
    <n-modal
      v-model:show="showCaptureModal"
      preset="card"
      class="search-capture-modal"
      style="width: min(640px, 92vw)"
      :title="t('search.captureTitle')"
    >
      <div class="space-y-4 p-1">
        <p class="text-sm text-slate-600 dark:text-slate-300">{{ t('search.captureHint') }}</p>

        <div class="space-y-1">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{{ t('search.captureQueryLabel') }}</h4>
          <div class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm break-words text-slate-800 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-100" data-testid="search-capture-query">
            {{ captureExecutedQuery }}
          </div>
        </div>

        <div class="space-y-1">
          <label class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400" for="search-capture-name">{{ t('search.captureNameLabel') }}</label>
          <n-input
            id="search-capture-name"
            v-model:value="captureName"
            :maxlength="255"
            :placeholder="t('search.captureNamePlaceholder')"
            data-testid="search-capture-name"
          />
        </div>

        <div class="space-y-2">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            {{ t('search.captureExpectedLabel') }}
          </h4>
          <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('search.captureExpectedHint') }}</p>
          <!-- 本次结果中的文档：直接勾选（相当于「本该命中这里」）。 -->
          <n-checkbox-group v-model:value="captureSelectedIds" data-testid="search-capture-results">
            <n-space vertical>
              <n-checkbox
                v-for="doc in captureResultDocs"
                :key="doc.id"
                :value="doc.id"
                :label="doc.label"
              />
            </n-space>
          </n-checkbox-group>
          <!-- 结果里没有期望文档时（bad case 的常见形态）：按组织文档远程搜索补充。 -->
          <n-select
            v-model:value="captureExtraIds"
            multiple
            filterable
            remote
            clearable
            :options="captureDocumentOptions"
            :loading="captureDocumentLoading"
            :placeholder="t('search.captureAddDocument')"
            :max-tag-count="3"
            data-testid="search-capture-extra-docs"
            @search="loadCaptureDocumentOptions"
          />
          <p v-if="captureSelectedIds.length === 0 && captureExtraIds.length === 0" class="text-xs text-amber-700 dark:text-amber-400" data-testid="search-capture-empty-warning">
            {{ t('search.captureExpectedRequired') }}
          </p>
          <p v-else-if="captureTooMany" class="text-xs text-amber-700 dark:text-amber-400" data-testid="search-capture-too-many">
            {{ t('search.captureTooMany', { max: CAPTURE_MAX_EXPECTED }) }}
          </p>
        </div>

        <p v-if="captureError" class="text-sm text-red-600 dark:text-red-400" role="alert" data-testid="search-capture-error">{{ captureError }}</p>

        <div class="flex justify-end gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
          <n-button round secondary :disabled="captureSaving" @click="showCaptureModal = false">
            {{ t('common.cancel') }}
          </n-button>
          <n-button
            type="primary"
            round
            :loading="captureSaving"
            :disabled="captureSaving || captureExpectedIds.length === 0 || captureTooMany"
            data-testid="search-capture-submit"
            @click="submitCapture"
          >
            <template #icon><n-icon><SaveOutline /></n-icon></template>
            {{ t('search.captureSubmit') }}
          </n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { NIcon, NSpin, NRadioGroup, NRadio, NSpace, NSelect, NTag, NButton, NModal, NSwitch, NInput, NCheckbox, NCheckboxGroup } from 'naive-ui'
import {
  SearchOutline, TimeOutline, ChatbubblesOutline, CopyOutline, SparklesOutline,
  DocumentTextOutline, DocumentOutline, GridOutline, NewspaperOutline, AlertCircleOutline, RefreshOutline, SaveOutline
} from '@vicons/ionicons5'
import { searchKnowledgeResponse, getSearchSuggestions, getSearchStats, buildSearchPayload } from '@/api/search'
import type { KnowledgeSearchResponse, SearchResult, SearchScopeItem, SearchStats } from '@/api/search'
import { listKnowledgeBases } from '@/api/knowledgeBases'
import { getKnowledgeBases } from '@/api/knowledge'
import type { KnowledgeBase } from '@/api/knowledge'
import { captureEvalCase } from '@/api/retrievalEval'
import { getErrorMessage } from '@/utils/apiError'
import { debounce } from 'lodash-es'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { DebugPanel } from '@/views/chat/components'
import type { RetrievalDebugData } from '@/types/chat'
import { buildDocumentRoute } from './search/documentLink'

const { t } = useI18n()
const userStore = useUserStore()
const router = useRouter()
const route = useRoute()
const message = useDedupedMessage()

// 搜索状态
const searchQuery = ref('')
const searchType = ref<'auto' | 'vector' | 'keyword' | 'hybrid'>('auto')
const selectedFileTypes = ref<string[]>([])
const selectedTags = ref<string[]>([])
const selectedKnowledgeBaseId = ref<string | null>(null)
const selectedDocumentIds = ref<string[]>([])
const topK = ref<number | null>(null)
const debugEnabled = ref(false)
const sortBy = ref<'relevance' | 'name'>('relevance')

// 知识库/标签候选（服务端按有效组织与 ACL 推导，客户端不传组织 id）
const knowledgeBaseOptions = ref<Array<{ label: string; value: string }>>([])
const knowledgeBaseLoading = ref(false)
const knowledgeBaseLoadFailed = ref(false)
const documentTagValues = ref<string[]>([])
const tagOptions = computed<Array<{ label: string; value: string }>>(() =>
  documentTagValues.value.map((tag) => ({ label: tag, value: tag }))
)
const fileTypeOptions = computed(() => [
  { label: t('search.fileTypeOptions.pdf'), value: 'pdf' },
  { label: t('search.fileTypeOptions.word'), value: 'word' },
  { label: t('search.fileTypeOptions.excel'), value: 'excel' },
  { label: t('search.fileTypeOptions.ppt'), value: 'ppt' },
  { label: t('search.fileTypeOptions.txt'), value: 'txt' },
  { label: t('search.fileTypeOptions.other'), value: 'other' },
])
// 结果状态
const searchResults = ref<SearchResult[]>([])
const lastResponse = ref<KnowledgeSearchResponse | null>(null)
const searchStats = ref<SearchStats | null>(null)
const suggestions = ref<string[]>([])
const selectedResult = ref<SearchResult | null>(null)
const showDetailModal = ref(false)
const loading = ref(false)
const hasSearched = ref(false)
const searchError = ref<string | null>(null)

// 文档范围选择（检索自测）
const documentOptions = ref<Array<{ label: string; value: string }>>([])
const documentLoading = ref(false)
let documentSeq = 0

const currentOrgId = computed(() => userStore.currentOrgId)

/** 当前是否带范围筛选；决定零命中的专属空态（不自动放宽重试）。 */
const hasActiveFilters = computed(() =>
  !!selectedKnowledgeBaseId.value ||
  selectedTags.value.length > 0 ||
  selectedFileTypes.value.length > 0 ||
  selectedDocumentIds.value.length > 0
)

/** KB 列表失败时降级为空选项，不清空用户已选 id、也不阻断搜索。 */
const loadKnowledgeBaseOptions = async () => {
  if (!currentOrgId.value) {
    knowledgeBaseOptions.value = []
    documentTagValues.value = []
    knowledgeBaseLoadFailed.value = false
    return
  }
  knowledgeBaseLoading.value = true
  try {
    const list = await listKnowledgeBases({ silent: true })
    knowledgeBaseOptions.value = (list.data ?? []).map((kb) => ({
      value: kb.id,
      label: kb.name,
    }))
    knowledgeBaseLoadFailed.value = false
  } catch {
    // KB 列表失败降级为空选项；已选 id 仍随搜索发送，由服务端验证归属。
    knowledgeBaseOptions.value = []
    knowledgeBaseLoadFailed.value = true
  } finally {
    knowledgeBaseLoading.value = false
  }
  // tags 候选取自当前组织可见文档（同一列表端点已做 ACL 与状态标注），
  // 不能按用户 id 预过滤——ACL 始终由服务端负责。失败时只剩自由输入。
  try {
    const docs = await getKnowledgeBases({ page: 1, page_size: 100 }, { silent: true })
    const payload = (docs.data as unknown as { data?: { data?: KnowledgeBase[] } })?.data
    const seen: string[] = []
    for (const doc of payload?.data ?? []) {
      for (const tag of doc.tags ?? []) {
        const value = tag.trim()
        if (value && !seen.includes(value)) seen.push(value)
      }
    }
    documentTagValues.value = seen.slice(0, 50)
  } catch {
    documentTagValues.value = []
  }
}

// 切组织：旧 KB id 属于前一租户，必须清空，并重新加载当前组织的 KB 候选。
watch(currentOrgId, async () => {
  selectedKnowledgeBaseId.value = null
  await nextTick()
  await loadKnowledgeBaseOptions()
})

// ── 保存为评测用例（bad-case 捕获）────────────────────────────────────────
// 设计口径：
// - 入口只在有检索结果时出现：这是「结果不理想」时的动作，空页给出只会误导；
// - 期望文档默认 = 本次结果涉及的文档（勾选式），另可按组织文档远程补充；
//   一个都没选时不提交（评测金标为空的用例没有任何意义）；
// - 提交后按后端 `created` 区分「已保存」与「同内容用例已存在」，后者不是
//   失败：后端幂等返回既有行，不产生重复样本。
const showCaptureModal = ref(false)
const captureName = ref('')
const captureSelectedIds = ref<string[]>([])
const captureExtraIds = ref<string[]>([])
const captureDocumentOptions = ref<Array<{ label: string; value: string }>>([])
const captureDocumentLoading = ref(false)
const captureSaving = ref(false)
const captureError = ref<string | null>(null)
let captureDocumentSeq = 0

interface CaptureDocOption { id: string; label: string }

/** 本次结果里可作期望金标的文档（按 document_id 去重，保持结果顺序）。 */
const captureResultDocs = computed<CaptureDocOption[]>(() => {
  const seen = new Set<string>()
  const docs: CaptureDocOption[] = []
  for (const result of searchResults.value) {
    const id = result.source?.document_id || result.id
    if (!id || seen.has(id)) continue
    seen.add(id)
    docs.push({ id, label: filenameOf(result) || t('search.unknownFile') })
  }
  return docs
})

/** 最终提交的金标集合（结果勾选 + 远程补充，去重保序）。 */
const captureExpectedIds = computed<string[]>(() => {
  const ids: string[] = []
  for (const id of [...captureSelectedIds.value, ...captureExtraIds.value]) {
    if (id && !ids.includes(id)) ids.push(id)
  }
  return ids
})

/** 后端 schema 上限（1–50）：超限时明确拦截，不静默截断用户的勾选。 */
const CAPTURE_MAX_EXPECTED = 50

const captureTooMany = computed(() => captureExpectedIds.value.length > CAPTURE_MAX_EXPECTED)

// 捕获的是「产生当前结果的那次检索」的 query，而不是输入框里尚未提交的新文本。
// 用户在出结果后改输入框、或清空输入框，都不能改变本次评测用例的语义。
const captureExecutedQuery = computed(() => (lastResponse.value?.query ?? '').trim())

const openCaptureModal = () => {
  captureName.value = ''
  captureSelectedIds.value = []
  captureExtraIds.value = []
  captureDocumentOptions.value = []
  captureError.value = null
  showCaptureModal.value = true
  void loadCaptureDocumentOptions()
}

const loadCaptureDocumentOptions = async (query = '') => {
  if (!currentOrgId.value) return
  const seq = ++captureDocumentSeq
  captureDocumentLoading.value = true
  try {
    // 与左侧文档范围选择同源（组织知识库列表；服务端已做 ACL 与状态标注）。
    const response = await getKnowledgeBases(
      { page: 1, page_size: 30, search: query || undefined },
      { silent: true }
    )
    if (seq !== captureDocumentSeq) return
    const payload = (response.data as unknown as { data?: { data?: KnowledgeBase[] } })?.data
    captureDocumentOptions.value = (payload?.data ?? []).map((doc) => ({
      value: doc.id,
      label: `${doc.title || doc.file_name}${isIndexedStatus(doc.status) ? '' : ` · ${scopeStatusLabel({ document_id: doc.id, found: true, status: doc.status })}`}`
    }))
  } catch {
    // 文档补充选择是增强能力：失败不阻塞「从本次结果勾选」的主路径。
  } finally {
    if (seq === captureDocumentSeq) captureDocumentLoading.value = false
  }
}

const submitCapture = async () => {
  const query = captureExecutedQuery.value
  const ids = captureExpectedIds.value
  if (!query) {
    captureError.value = t('search.captureQueryMissing')
    return
  }
  if (ids.length === 0) {
    captureError.value = t('search.captureExpectedRequired')
    return
  }
  if (ids.length > CAPTURE_MAX_EXPECTED) {
    captureError.value = t('search.captureTooMany', { max: CAPTURE_MAX_EXPECTED })
    return
  }
  captureSaving.value = true
  captureError.value = null
  try {
    const result = await captureEvalCase({
      query,
      expected_document_ids: ids,
      ...(captureName.value.trim() ? { name: captureName.value.trim() } : {})
    })
    showCaptureModal.value = false
    if (result.created) {
      message.success(t('search.captureSuccess'))
    } else {
      // 幂等命中既有用例：不是失败，明确告诉用户没有新建重复行。
      message.info(t('search.captureDuplicate', { name: result.case?.name ?? '' }))
    }
  } catch (err) {
    // 校验/权限错误展示后端语义原文；端点无 X-Silent-Error，全局拦截器已
    // 输出过通用提示，这里只在弹窗内给出可读原因（不重复弹第二个 toast）。
    captureError.value = getErrorMessage(err, t('search.captureFailed'))
  } finally {
    captureSaving.value = false
  }
}

const sortOptions = computed(() => [
  { label: t('search.relevance'), value: 'relevance' },
  { label: t('search.name'), value: 'name' }
])
const topKOptions = [
  { label: '5', value: 5 },
  { label: '10', value: 10 },
  { label: '20', value: 20 },
  { label: '50', value: 50 }
]

const modeLabels = computed<Record<string, string>>(() => ({
  auto: t('search.auto'),
  hybrid: t('search.hybrid'),
  vector: t('search.semantic'),
  keyword: t('search.keyword')
}))
const searchModeLabel = computed(() => {
  const mode = lastResponse.value?.search_mode
  return mode ? (modeLabels.value[mode] ?? mode) : ''
})

// ── 结果字段访问器（真实响应形状见 api/search.ts）────────────────────────
const filenameOf = (r: SearchResult): string => r.source?.filename ?? r.metadata?.filename ?? ''
const contentOf = (r: SearchResult): string => r.content || r.metadata?.snippet || r.text || ''
const isUnverifiedText = (r: SearchResult): boolean =>
  !!contentOf(r) && !(!!r.content && r.text_provenance === 'parser_original')
const isManualOverride = (r: SearchResult): boolean =>
  r.retrieval_text_provenance === 'manual_override'
const rerankScoreOf = (r: SearchResult): string | null =>
  typeof r.relevance_score === 'number' ? r.relevance_score.toFixed(2) : null
const fileTypeOf = (r: SearchResult): string => {
  const name = filenameOf(r)
  const dot = name.lastIndexOf('.')
  return dot > -1 ? name.slice(dot + 1).toLowerCase() : ''
}
const sectionLabelOf = (r: SearchResult): string => {
  if (r.section_title) return r.section_title
  if (r.clause_no?.length) {
    const nos = r.clause_no
    const label = nos.length > 1 ? `${nos[0]}–${nos[nos.length - 1]}` : String(nos[0])
    return t('search.clauseLabel', { no: label })
  }
  if (r.section_no?.length) return r.section_no.join('、')
  return ''
}

const sortedResults = computed(() => {
  const results = [...searchResults.value]
  if (sortBy.value === 'relevance') return results
  if (sortBy.value === 'name') return results.sort((a, b) => filenameOf(a).localeCompare(filenameOf(b)))
  return results
})

const getFileTypeIcon = (type?: string) => {
  const map: Record<string, unknown> = { pdf: DocumentOutline, docx: DocumentTextOutline, xlsx: GridOutline, txt: DocumentTextOutline, md: NewspaperOutline }
  return map[(type ?? '').toLowerCase()] || DocumentOutline
}

// 空结果诊断：先看所选文档状态，再看组织索引状态，最后才说「没匹配到」。
// 'completed' 是状态词表收敛前的列表旧词（api/knowledge.ts），按已索引处理。
const isIndexedStatus = (status?: string | null): boolean =>
  status === 'indexed' || status === 'completed'

const emptyState = computed<{ title: string; desc: string; scope?: SearchScopeItem[] }>(() => {
  const response = lastResponse.value
  if (!response) return { title: t('search.noResults'), desc: t('search.noResultsDesc') }
  // 带筛选的零命中：明确告诉用户是范围太窄，绝不自动去掉筛选重试。
  if (hasActiveFilters.value) {
    return { title: t('search.filteredEmpty'), desc: t('search.filteredEmptyDesc') }
  }
  const scope = response.document_scope ?? []
  const notReady = scope.filter((item) => !item.found || !isIndexedStatus(item.status))
  if (notReady.length) {
    return { title: t('search.docScopeTitle'), desc: t('search.docNotIndexed'), scope }
  }
  switch (response.index_status) {
    case 'empty_index':
      return { title: t('search.indexEmpty'), desc: t('search.indexEmptyDesc') }
    case 'index_error':
      return { title: t('search.indexError'), desc: t('search.indexErrorDesc') }
    case 'unknown':
      return { title: t('search.indexUnknown'), desc: t('search.indexUnknownDesc') }
    default:
      if (response.index_doc_count != null) {
        return { title: t('search.noResults'), desc: t('search.indexHasData', { count: response.index_doc_count }) }
      }
      return { title: t('search.noResults'), desc: t('search.noResultsDesc') }
  }
})

const scopeStatusLabel = (item: SearchScopeItem): string => {
  if (!item.found) return t('search.docNotFound')
  if (isIndexedStatus(item.status)) return t('search.docStatusIndexed')
  if (item.status === 'failed') return t('search.docStatusFailed')
  return t('search.docStatusProcessing')
}

// 请求序号：并发搜索只认最后一次。
let searchSeq = 0

const handleSearch = async () => {
  if (!searchQuery.value.trim()) return
  // 提交后立即收起建议浮层：否则它会盖住结果区并拦截点击。
  suggestionSeq += 1
  handleInputChange.cancel()
  suggestions.value = []
  const seq = ++searchSeq
  const orgId = currentOrgId.value
  searchError.value = null
  hasSearched.value = true
  if (!orgId) {
    searchResults.value = []
    lastResponse.value = null
    searchError.value = t('search.noOrganization')
    loading.value = false
    return
  }
  loading.value = true
  try {
    const response = await searchKnowledgeResponse(buildSearchPayload({
      query: searchQuery.value,
      organizationId: orgId,
      topK: topK.value ?? undefined,
      searchMode: searchType.value,
      fileTypes: selectedFileTypes.value,
      tags: selectedTags.value,
      knowledgeBaseId: selectedKnowledgeBaseId.value ?? undefined,
      documentIds: selectedDocumentIds.value,
      debug: debugEnabled.value
    }))
    if (seq !== searchSeq) return
    lastResponse.value = response
    searchResults.value = response.results || []
  } catch (err) {
    if (seq !== searchSeq) return
    searchResults.value = []
    lastResponse.value = null
    searchError.value = getErrorMessage(err, t('search.searchErrorDesc'))
  } finally {
    if (seq === searchSeq) loading.value = false
  }
}

// 建议词：输入防抖 + 请求序号（提交搜索会使在途建议失效，避免浮层盖住结果）。
let suggestionSeq = 0
const handleInputChange = debounce(async () => {
  const seq = ++suggestionSeq
  if (!searchQuery.value.trim() || !currentOrgId.value) { suggestions.value = []; return }
  const list = await getSearchSuggestions(searchQuery.value, currentOrgId.value)
  if (seq !== suggestionSeq) return
  suggestions.value = list
}, 300)

// 失焦后稍等再收起，避免点击建议项时先被 blur 清掉（建议项另有 mousedown.prevent）。
const hideSuggestions = () => {
  if (!suggestions.value.length) return
  window.setTimeout(() => {
    suggestions.value = []
  }, 150)
}

const selectSuggestion = (s: string) => { searchQuery.value = s; suggestions.value = []; handleSearch() }

const loadDocumentOptions = async (query = '') => {
  if (!currentOrgId.value) return
  const seq = ++documentSeq
  documentLoading.value = true
  try {
    const response = await getKnowledgeBases(
      { page: 1, page_size: 20, search: query || undefined },
      { silent: true }
    )
    if (seq !== documentSeq) return
    const payload = (response.data as unknown as { data?: { data?: KnowledgeBase[] } })?.data
    documentOptions.value = (payload?.data ?? []).map((doc) => ({
      value: doc.id,
      label: `${doc.title || doc.file_name}${isIndexedStatus(doc.status) ? '' : ` · ${scopeStatusLabel({ document_id: doc.id, found: true, status: doc.status })}`}`
    }))
  } catch {
    // 文档选择器是增强能力：失败不影响检索本身。
  } finally {
    if (seq === documentSeq) documentLoading.value = false
  }
}

const clearFilters = () => {
  searchType.value = 'auto'
  selectedFileTypes.value = []
  selectedTags.value = []
  selectedKnowledgeBaseId.value = null
  sortBy.value = 'relevance'
  selectedDocumentIds.value = []
  topK.value = null
  debugEnabled.value = false
}

const showResultDetail = (r: SearchResult) => { selectedResult.value = r; showDetailModal.value = true }

// 卡片整体可键盘打开，但内部按钮（问问 AI/打开文档页/复制）必须保留自己的
// 键盘激活：只有事件目标就是卡片时才拦截，否则让按钮的 Enter/Space 正常生效。
const handleCardActivate = (event: KeyboardEvent, r: SearchResult) => {
  if (event.target !== event.currentTarget) return
  event.preventDefault()
  showResultDetail(r)
}

// 检索命中 → 文档独立页并定位分块；链接可分享，不再止步于页内弹窗。
const openOriginal = (r: SearchResult) => {
  const target = buildDocumentRoute(r)
  if (!target) return
  void router.push(target)
}

const askAI = (r: SearchResult) => {
  const filename = filenameOf(r)
  const question = searchQuery.value.trim()
  const q = filename
    ? question
      ? t('search.askAIQuestion', { filename, query: question })
      : t('search.askAIQuestionGeneric', { filename })
    : question
  const query: Record<string, string> = {}
  if (q) query.q = q
  const snippet = contentOf(r).trim()
  if (snippet) query.context = snippet.slice(0, 300)
  router.push({ name: 'Chat', query })
}

const copyResultContent = async (r: SearchResult | null) => {
  if (!r) return
  await navigator.clipboard.writeText(contentOf(r))
  message.success(t('common.copySuccess'))
}

const applyQueryFromRoute = () => {
  const q = route.query.q
  if (typeof q !== 'string' || !q.trim()) return
  if (q === searchQuery.value && hasSearched.value) return
  searchQuery.value = q
  handleSearch()
}

watch(() => route.query.q, applyQueryFromRoute)

onMounted(async () => {
  if (currentOrgId.value) {
    searchStats.value = await getSearchStats(currentOrgId.value)
    void loadDocumentOptions()
    void loadKnowledgeBaseOptions()
  }
  applyQueryFromRoute()
})
</script>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(37, 99, 235, 0.1); border-radius: 10px; }
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

@media (max-width: 1023px) {
  /* 窄屏：页面整体滚动，筛选/结果不再是固定高度内的两个滚动区
     （此前筛选卡 514px 高会把结果区压成 2px）。结果排在筛选之前，
     搜索后不必先滚过整张筛选卡。 */
  .search-page { display: block; height: auto; min-height: 100%; overflow-y: auto; }
  .search-layout { flex: none; overflow: visible; }
  .search-results { order: 1; overflow: visible; }
  .search-filter { order: 2; }
  .search-results-scroll { overflow: visible; }
}

/* 平板（含触控）也需要 44px：结果卡与筛选控件不能只在手机断点放大。 */
@media (max-width: 1024px) {
  .result-action.n-button { min-height: 44px; min-width: 44px; }
  .search-filter :deep(.n-base-selection) { --n-height: 44px !important; }
  .search-filter :deep(.n-radio) { min-height: 44px; align-items: center; }
  .search-filter :deep(.n-switch) { min-height: 44px; display: inline-flex; align-items: center; }
  .search-filter :deep(.n-button) { min-height: 44px; }
}

@media (max-width: 767px) {
  .search-page { padding: 1rem; }
  .search-stats { flex-wrap: wrap; }
  .search-submit { min-height: 44px; padding-left: 1rem; padding-right: 1rem; }
  .search-layout { flex-direction: column; gap: 1rem; }
  .search-filter { width: 100%; flex: none; overflow: visible; padding-right: 0; }
  .search-results { min-height: 0; }
  .suggestion-item { min-height: 44px; }
}
</style>

<style>
/* 弹窗 teleport 到 body 后 scoped 选择器失效：用弹窗根类约束触控高度。 */
@media (max-width: 1024px) {
  .search-result-detail-modal .n-button {
    min-height: 44px;
  }
  .search-result-detail-modal .n-base-close.n-card-header__close {
    width: 44px;
    height: 44px;
  }
}
</style>
