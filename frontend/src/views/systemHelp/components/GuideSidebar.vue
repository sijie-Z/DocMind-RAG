<!--
  帮助中心左侧目录：搜索框 + 可折叠分组 + 文档列表。
  纯展示组件：搜索词由父级持有（桌面侧栏与移动抽屉共享同一份过滤结果），
  选中文档通过 select 事件交给父级处理。
-->
<template>
  <nav :aria-label="t('help.center.navTitle')" class="space-y-4">
    <n-input
      data-testid="help-search"
      :value="search"
      clearable
      :placeholder="t('help.center.searchPlaceholder')"
      :input-props="{ 'aria-label': t('help.center.searchPlaceholder') }"
      @update:value="(value: string) => $emit('update:search', value)"
    >
      <template #prefix>
        <n-icon :size="16" class="text-gray-400"><SearchOutline /></n-icon>
      </template>
    </n-input>

    <p v-if="search.trim() && total > 0" data-testid="help-search-count" class="text-xs text-gray-500 dark:text-gray-400">
      {{ t('help.center.searchResults', { count: total }) }}
    </p>

    <div
      v-if="search.trim() && total === 0"
      data-testid="help-search-empty"
      class="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400"
    >
      {{ t('help.center.searchNoResults') }}
    </div>

    <div v-for="group in groups" :key="group.id" class="space-y-1">
      <button
        type="button"
        :data-testid="`help-group-${group.id}`"
        :disabled="Boolean(search.trim())"
        class="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500 disabled:cursor-default disabled:opacity-70 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
        :aria-expanded="expanded(group.id)"
        @click="toggle(group.id)"
      >
        <span>{{ t(group.labelKey) }}</span>
        <n-icon :size="14" class="transition-transform" :class="{ 'rotate-90': expanded(group.id) }">
          <ChevronForwardOutline />
        </n-icon>
      </button>
      <ul v-if="expanded(group.id)" class="space-y-0.5">
        <li v-for="doc in group.docs" :key="doc.id">
          <button
            type="button"
            :data-testid="`help-doc-${doc.id}`"
            class="w-full rounded-lg px-3 py-2 text-left text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
            :class="
              doc.id === activeId
                ? 'bg-emerald-50 text-emerald-700 font-medium dark:bg-emerald-900/30 dark:text-emerald-300'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
            "
            :aria-current="doc.id === activeId ? 'page' : undefined"
            @click="$emit('select', doc.id)"
          >
            {{ t(doc.titleKey) }}
          </button>
        </li>
      </ul>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NIcon, NInput } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { ChevronForwardOutline, SearchOutline } from '@vicons/ionicons5'
import type { GuideNavGroup } from '@/content/guide'

const props = defineProps<{
  groups: GuideNavGroup[]
  activeId: string
  search: string
  total: number
}>()

defineEmits<{
  (e: 'update:search', value: string): void
  (e: 'select', id: string): void
}>()

const { t } = useI18n()

const collapsed = ref<Record<string, boolean>>({})

/** 搜索时强制展开所有分组，避免命中项藏在折叠组里 */
const expanded = (id: string): boolean => (props.search.trim() ? true : !collapsed.value[id])

const toggle = (id: string): void => {
  if (props.search.trim()) return
  collapsed.value = { ...collapsed.value, [id]: !collapsed.value[id] }
}
</script>
