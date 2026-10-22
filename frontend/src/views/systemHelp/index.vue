<!--
  使用文档（帮助中心）：左侧分组目录 + 右侧 Markdown 正文。
  正文来自 @/content/guide/*.md（Vite ?raw），复用 markdownRenderer（经 Markdown.vue），
  UI 文案全部走 i18n；移动端目录收进自绘抽屉。
-->
<template>
  <div ref="rootEl" data-testid="help-center" class="h-full overflow-y-auto bg-gray-50 dark:bg-gray-950">
    <div class="mx-auto max-w-7xl p-4 lg:p-8">
      <PageBreadcrumb />

      <header class="mt-4 mb-6 flex items-start gap-4">
        <div class="hidden sm:flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-2xl bg-emerald-500/10">
          <n-icon size="26" class="text-emerald-500"><HelpCircleOutline /></n-icon>
        </div>
        <div class="min-w-0 flex-1">
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">{{ t('menu.systemHelp') }}</h1>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{{ t('help.center.subtitle') }}</p>
        </div>
        <n-button
          v-if="docs.length > 0"
          class="lg:hidden flex-shrink-0"
          data-testid="help-nav-toggle"
          quaternary
          circle
          :aria-label="t('help.center.openNav')"
          :aria-expanded="mobileNavOpen"
          aria-controls="help-mobile-nav-dialog"
          @click="openMobileNav"
        >
          <template #icon>
            <n-icon><MenuOutline /></n-icon>
          </template>
        </n-button>
      </header>

      <div class="lg:grid lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-8">
        <aside v-if="docs.length > 0 && !mobileNavOpen" data-testid="help-nav" class="hidden lg:block">
          <div class="sticky top-4 rounded-2xl border border-gray-100 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
            <GuideSidebar
              :groups="navGroups"
              :active-id="activeDoc?.id ?? ''"
              :search="searchQuery"
              :total="filteredDocs.length"
              @update:search="searchQuery = $event"
              @select="selectDoc"
            />
          </div>
        </aside>

        <main data-testid="help-main" class="min-w-0" :class="{ 'lg:col-span-2': !docs.length || mobileNavOpen }">
          <div
            v-if="docs.length === 0"
            data-testid="help-empty"
            class="rounded-2xl border border-gray-100 bg-white px-6 py-16 dark:border-gray-700 dark:bg-gray-800"
          >
            <n-empty :description="t('help.center.emptyTitle')">
              <template #extra>
                <p class="max-w-md text-center text-xs text-gray-500 dark:text-gray-400">
                  {{ t('help.center.emptyDesc') }}
                </p>
              </template>
            </n-empty>
          </div>

          <article
            v-else-if="activeDoc"
            class="overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800"
          >
            <div class="border-b border-gray-100 px-6 py-5 dark:border-gray-700">
              <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h2 class="text-xl font-semibold text-gray-900 dark:text-white">{{ t(activeDoc.titleKey) }}</h2>
                <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-500 dark:bg-gray-700/60 dark:text-gray-400">
                  {{ t('help.center.bodyLanguageNote') }}
                </span>
              </div>
            </div>

            <div class="p-6">
              <div v-if="!hasContent" data-testid="help-content-missing">
                <n-alert type="warning" :title="t('help.center.contentMissing')" />
              </div>
              <template v-else>
                <div
                  v-if="headings.length > 0"
                  data-testid="help-toc"
                  class="mb-6 rounded-xl border border-gray-100 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/40"
                >
                  <p class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    {{ t('help.center.tocTitle') }}
                  </p>
                  <ul class="space-y-1">
                    <li v-for="heading in headings" :key="heading.id" :style="{ paddingLeft: heading.level === 3 ? '1rem' : '0' }">
                      <a
                        :href="anchorHref(heading.id)"
                        class="text-sm text-blue-600 hover:underline dark:text-blue-400"
                        @click.prevent="jumpTo(heading.id)"
                      >{{ heading.text }}</a>
                    </li>
                  </ul>
                </div>
                <div ref="contentEl" data-testid="help-content">
                  <Markdown :content="activeDoc.content" />
                </div>
              </template>
            </div>

            <footer class="flex items-center justify-between gap-3 border-t border-gray-100 px-6 py-4 dark:border-gray-700">
              <n-button
                data-testid="help-prev"
                :disabled="!prevDoc"
                secondary
                @click="prevDoc && selectDoc(prevDoc.id)"
              >
                <template #icon>
                  <n-icon><ArrowBackOutline /></n-icon>
                </template>
                {{ t('help.center.prev') }}
              </n-button>
              <n-button
                data-testid="help-next"
                :disabled="!nextDoc"
                secondary
                @click="nextDoc && selectDoc(nextDoc.id)"
              >
                {{ t('help.center.next') }}
                <template #icon>
                  <n-icon><ArrowForwardOutline /></n-icon>
                </template>
              </n-button>
            </footer>
          </article>
        </main>
      </div>
    </div>

    <div
      v-if="mobileNavOpen"
      id="help-mobile-nav-dialog"
      ref="drawerEl"
      data-testid="help-mobile-nav"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      :aria-label="t('help.center.navTitle')"
      class="fixed inset-0 z-40 outline-none lg:hidden"
      @keydown="onDrawerKeydown"
    >
      <div data-testid="help-nav-overlay" class="absolute inset-0 bg-black/40" @click="closeMobileNav"></div>
      <div class="absolute inset-y-0 left-0 flex w-80 max-w-[85vw] flex-col bg-white shadow-xl dark:bg-gray-900">
        <div class="flex items-center justify-between border-b border-gray-100 px-4 py-3 dark:border-gray-700">
          <span class="text-sm font-semibold text-gray-900 dark:text-white">{{ t('help.center.title') }}</span>
          <n-button
            data-testid="help-nav-close"
            quaternary
            circle
            :aria-label="t('help.center.closeNav')"
            @click="closeMobileNav"
          >
            <template #icon>
              <n-icon><CloseOutline /></n-icon>
            </template>
          </n-button>
        </div>
        <div class="flex-1 overflow-y-auto overscroll-contain p-4">
          <GuideSidebar
            :groups="navGroups"
            :active-id="activeDoc?.id ?? ''"
            :search="searchQuery"
            :total="filteredDocs.length"
            @update:search="searchQuery = $event"
            @select="selectDoc"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NEmpty, NIcon } from 'naive-ui'
import {
  ArrowBackOutline,
  ArrowForwardOutline,
  CloseOutline,
  HelpCircleOutline,
  MenuOutline
} from '@vicons/ionicons5'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import Markdown from '@/components/common/Markdown.vue'
import GuideSidebar from './components/GuideSidebar.vue'
import { GUIDE_DOCS, GUIDE_GROUP_ORDER, type GuideDoc, type GuideGroupId, type GuideNavGroup } from '@/content/guide'

const props = withDefaults(defineProps<{ docs?: GuideDoc[] }>(), {
  docs: () => GUIDE_DOCS
})

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const GROUP_LABELS: Record<GuideGroupId, string> = {
  gettingStarted: 'help.center.groups.gettingStarted',
  concepts: 'help.center.groups.concepts',
  guides: 'help.center.groups.guides',
  faq: 'help.center.groups.faq',
  troubleshooting: 'help.center.groups.troubleshooting'
}

const GROUP_ORDER: Array<{ id: GuideGroupId; labelKey: string }> = GUIDE_GROUP_ORDER.map((id) => ({
  id,
  labelKey: GROUP_LABELS[id]
}))

const searchQuery = ref('')
const mobileNavOpen = ref(false)
const activeDocId = ref<string>('')
const rootEl = ref<HTMLElement | null>(null)
const contentEl = ref<HTMLElement | null>(null)
const drawerEl = ref<HTMLElement | null>(null)
const lastFocused = ref<HTMLElement | null>(null)
const headings = ref<Array<{ id: string; text: string; level: number }>>([])

const docs = computed(() => props.docs)
const activeDoc = computed<GuideDoc | null>(
  () => docs.value.find((doc) => doc.id === activeDocId.value) ?? docs.value[0] ?? null
)
const hasContent = computed(() => Boolean(activeDoc.value?.content.trim()))

/** 粗略剥离 Markdown 语法，用于正文关键词搜索；保留标识符里的 _ 与 -，代码内容可被搜索 */
function stripMarkdown(content: string): string {
  return content
    .replace(/```[^\n]*\n?/g, ' ')
    .replace(/`/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^>\s?/gm, '')
    .replace(/[*~]/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
}

const normalizedSearch = computed(() => searchQuery.value.trim().toLowerCase())

const filteredDocs = computed(() => {
  const query = normalizedSearch.value
  if (!query) return docs.value
  return docs.value.filter((doc) => {
    const title = t(doc.titleKey).toLowerCase()
    return title.includes(query) || stripMarkdown(doc.content).toLowerCase().includes(query)
  })
})

const navGroups = computed<GuideNavGroup[]>(() =>
  GROUP_ORDER.map((group) => ({
    ...group,
    docs: filteredDocs.value.filter((doc) => doc.group === group.id)
  })).filter((group) => !normalizedSearch.value || group.docs.length > 0)
)

/** 上/下一篇跟随当前搜索结果：过滤后只剩一篇时两端都禁用 */
const activeIndex = computed(() => filteredDocs.value.findIndex((doc) => doc.id === activeDoc.value?.id))
const prevDoc = computed<GuideDoc | null>(
  () => (activeIndex.value > 0 ? filteredDocs.value[activeIndex.value - 1] : null)
)
const nextDoc = computed<GuideDoc | null>(
  () =>
    activeIndex.value >= 0 && activeIndex.value < filteredDocs.value.length - 1
      ? filteredDocs.value[activeIndex.value + 1]
      : null
)

function selectDoc(id: string): void {
  if (!docs.value.some((doc) => doc.id === id)) return
  activeDocId.value = id
  if (mobileNavOpen.value) closeMobileNav()
  if (route.query.doc !== id || route.query.anchor !== undefined) {
    router.replace({ query: { ...route.query, doc: id, anchor: undefined } })
  }
}

function openMobileNav(): void {
  lastFocused.value = typeof document !== 'undefined' ? (document.activeElement as HTMLElement | null) : null
  mobileNavOpen.value = true
  nextTick(() => drawerEl.value?.focus())
}

function closeMobileNav(): void {
  mobileNavOpen.value = false
  nextTick(() => lastFocused.value?.focus?.())
}

/** 抽屉内键盘约定：Escape 关闭；Tab 在抽屉内循环（含焦点落在容器上的初始态） */
function onDrawerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeMobileNav()
    return
  }
  if (event.key !== 'Tab' || !drawerEl.value) return
  const focusables = Array.from(
    drawerEl.value.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  )
  if (focusables.length === 0) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (!active || active === drawerEl.value || !drawerEl.value.contains(active)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
    return
  }
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

/** 窗口跨过 lg 断点时关闭抽屉，避免桌面端出现「无侧栏」的窄栏文章 */
function onDesktopBreakpoint(event: MediaQueryListEvent | MediaQueryList): void {
  if (event.matches && mobileNavOpen.value) closeMobileNav()
}

let desktopQuery: MediaQueryList | null = null
let priorOverflow: { body: string; root: string } | null = null

watch(mobileNavOpen, (open) => {
  if (typeof document === 'undefined') return
  if (open) {
    priorOverflow = {
      body: document.body.style.overflow,
      root: rootEl.value?.style.overflow ?? ''
    }
    document.body.style.overflow = 'hidden'
    if (rootEl.value) rootEl.value.style.overflow = 'hidden'
    document.addEventListener('keydown', onGlobalEscape)
  } else {
    document.body.style.overflow = priorOverflow?.body ?? ''
    if (rootEl.value) rootEl.value.style.overflow = priorOverflow?.root ?? ''
    priorOverflow = null
    document.removeEventListener('keydown', onGlobalEscape)
  }
})

/** 焦点若被移出抽屉（如 Shift+Tab 逃逸），仍可用 Escape 关闭 */
function onGlobalEscape(event: KeyboardEvent): void {
  if (event.key === 'Escape' && mobileNavOpen.value) closeMobileNav()
}

onBeforeUnmount(() => {
  if (typeof document !== 'undefined') {
    document.body.style.overflow = priorOverflow?.body ?? ''
    document.removeEventListener('keydown', onGlobalEscape)
  }
  if (rootEl.value && priorOverflow) rootEl.value.style.overflow = priorOverflow.root
  priorOverflow = null
  desktopQuery?.removeEventListener?.('change', onDesktopBreakpoint)
})

/** TOC 链接写成可分享的查询串（与页内锚点机制一致），支持 Ctrl/Cmd 点击新开 */
function anchorHref(id: string): string {
  return router.resolve({
    query: { ...route.query, doc: activeDoc.value?.id, anchor: id }
  }).href
}

function slugify(text: string): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  return base || 'section'
}

function scrollToHeading(id: string): void {
  const target = Array.from(contentEl.value?.querySelectorAll<HTMLElement>('h2, h3') ?? []).find(
    (element) => element.id === id
  )
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

function jumpTo(id: string): void {
  scrollToHeading(id)
  router.replace({ query: { ...route.query, doc: activeDoc.value?.id, anchor: id } })
}

/**
 * 渲染后给 h2/h3 补稳定 id，并从 DOM 反推页内目录。
 * 从 DOM 取文本而不是从 Markdown 原文猜，保证目录与正文严格一致。
 */
async function annotateHeadings(): Promise<void> {
  await nextTick()
  const root = contentEl.value
  if (!root) {
    headings.value = []
    return
  }
  const used = new Set<string>()
  const list: Array<{ id: string; text: string; level: number }> = []
  for (const element of Array.from(root.querySelectorAll<HTMLElement>('h2, h3'))) {
    const text = (element.textContent ?? '').trim()
    const base = slugify(text)
    let id = base
    let suffix = 2
    while (used.has(id)) {
      id = `${base}-${suffix}`
      suffix += 1
    }
    used.add(id)
    element.id = id
    list.push({ id, text, level: Number(element.tagName.slice(1)) })
  }
  headings.value = list

  const anchor = route.query.anchor
  if (typeof anchor === 'string' && anchor) {
    scrollToHeading(anchor)
  }
}

onMounted(() => {
  const requested = typeof route.query.doc === 'string' ? route.query.doc : ''
  const valid = docs.value.some((doc) => doc.id === requested)
  activeDocId.value = valid ? requested : docs.value[0]?.id ?? ''
  // 无效的 ?doc= 不留在线址栏里，避免分享出与实际内容不符的链接
  if (requested && !valid && activeDocId.value) {
    router.replace({ query: { ...route.query, doc: activeDocId.value } })
  }
  annotateHeadings()
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    desktopQuery = window.matchMedia('(min-width: 1024px)')
    desktopQuery.addEventListener?.('change', onDesktopBreakpoint)
  }
})

watch(
  () => route.query.doc,
  (value) => {
    if (typeof value === 'string' && docs.value.some((doc) => doc.id === value) && value !== activeDocId.value) {
      activeDocId.value = value
    }
  }
)

watch(
  () => activeDoc.value?.id,
  () => {
    // 切换文档后回到顶部；若 URL 带 anchor，annotateHeadings 会随后滚到目标标题
    if (rootEl.value) rootEl.value.scrollTop = 0
    annotateHeadings()
  },
  { flush: 'post' }
)
</script>
