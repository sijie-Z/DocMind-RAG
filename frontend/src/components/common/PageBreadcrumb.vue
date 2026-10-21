<!--
  T9 定位三件套 · 深页面包屑（DocMind → /dashboard / 归属菜单项 / 当前页）。

  - 键与目标一律来自单一事实源：归属项经 config/menu.ts（route.meta.activeMenu），
    当前页取 route.meta.title，品牌项固定 "DocMind"（与侧栏 logo/顶栏品牌同字）。
  - showBack：在归属 ≠ 当前页的深页（图谱/编辑器/记忆）额外渲染"返回"出口，
    一键回到归属菜单面（不再只能按浏览器后退）。
  - 本组件无硬编码中文（CJK 棘轮守护），品牌字为全语同字的品牌名。
-->
<template>
  <nav class="flex flex-wrap items-center gap-2 text-[13px] leading-5" aria-label="Breadcrumb">
    <n-button
      v-if="showBack && backTarget"
      text
      size="tiny"
      class="flex-shrink-0"
      @click="router.push({ name: backTarget })"
    >
      <template #icon>
        <n-icon size="14"><ArrowBackOutline /></n-icon>
      </template>
      {{ t('common.back') }}
    </n-button>

    <template v-for="(item, index) in chain" :key="index">
      <span v-if="index > 0" class="select-none text-[var(--c-text-4)]" aria-hidden="true">/</span>
      <router-link
        v-if="item.routeName && index < chain.length - 1"
        :to="{ name: item.routeName }"
        class="text-[var(--c-text-4)] transition-colors hover:text-[var(--c-primary)]"
      >
        {{ itemLabel(item) }}
      </router-link>
      <span
        v-else
        :class="
          index === chain.length - 1
            ? 'font-medium text-[var(--c-text-2)]'
            : 'text-[var(--c-text-4)]'
        "
      >
        {{ itemLabel(item) }}
      </span>
    </template>
  </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NButton, NIcon } from 'naive-ui'
import { ArrowBackOutline } from '@vicons/ionicons5'
import {
  breadcrumbChainFor,
  effectiveActiveMenu,
  type BreadcrumbItem,
} from '@/config/navigation'

withDefaults(defineProps<{ showBack?: boolean }>(), { showBack: false })

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const chain = computed<BreadcrumbItem[]>(() =>
  breadcrumbChainFor(route.name as string, route.meta),
)

/** 返回目标：生效高亮键 ≠ 当前页时指向归属菜单面（图谱→知识库、编辑器/记忆→Agent 工作流） */
const backTarget = computed<string | null>(() => {
  const effective = effectiveActiveMenu(route.name as string, route.meta.activeMenu)
  return effective && effective !== route.name ? effective : null
})

const itemLabel = (item: BreadcrumbItem): string => (item.i18nKey ? t(item.i18nKey) : 'DocMind')
</script>
