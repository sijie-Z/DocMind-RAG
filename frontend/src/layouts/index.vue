<template>
  <div class="h-screen flex bg-gray-50 dark:bg-gray-950">
    <!-- 移动端遮罩层 -->
    <div
      v-if="mobileMenuOpen"
      class="fixed inset-0 bg-black/40 z-40 md:hidden"
      @click="mobileMenuOpen = false"
    />

    <!-- 侧边栏 -->
    <div
      class="bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col transition-all duration-300"
      :class="[
        appStore.sidebarCollapsed ? 'w-20' : 'w-64',
        'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50',
        mobileMenuOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full',
      ]"
    >
      <!-- Logo：DocMind 图标 + 名称（T9 起可点回仪表盘/首页） -->
      <div
        class="h-16 flex items-center px-4 border-b border-gray-100 dark:border-gray-800/50 bg-white/50 dark:bg-gray-900/50 backdrop-blur-sm overflow-hidden whitespace-nowrap cursor-pointer transition-opacity hover:opacity-80"
        role="link"
        tabindex="0"
        @click="router.push({ name: 'Dashboard' })"
        @keydown.enter="router.push({ name: 'Dashboard' })"
      >
        <div class="flex items-center space-x-3 min-w-0">
          <div class="flex-shrink-0 w-9 h-9 bg-gradient-to-br from-slate-600 to-blue-600 rounded-xl flex items-center justify-center text-white font-bold text-sm tracking-tight shadow-lg shadow-slate-500/20">
            DM
          </div>
          <span v-if="!appStore.sidebarCollapsed" class="text-lg font-extrabold tracking-tight bg-gradient-to-r from-slate-700 to-blue-700 dark:from-slate-400 dark:to-blue-400 bg-clip-text text-transparent">
            DocMind
          </span>
        </div>
      </div>
      
      <!-- 导航菜单 -->
      <nav class="flex-1 py-4">
        <n-menu
          :collapsed="appStore.sidebarCollapsed"
          :collapsed-width="80"
          :collapsed-icon-size="22"
          :options="menuOptions"
          :value="activeKey"
          :node-props="menuNodeProps"
          @update:value="handleMenuSelect"
        />
      </nav>
      
      <!-- 底部折叠按钮（桌面端） -->
      <div class="p-4 border-t border-gray-100 dark:border-gray-800 justify-center hidden md:flex">
        <n-button quaternary circle @click="appStore.toggleSidebar">
          <template #icon>
            <n-icon size="20">
              <ChevronBackOutline v-if="!appStore.sidebarCollapsed" />
              <ChevronForwardOutline v-else />
            </n-icon>
          </template>
        </n-button>
      </div>

      <!-- 语言和主题切换 -->
      <div class="px-4 py-2 border-t border-gray-100 dark:border-gray-800 flex items-center justify-center gap-2">
        <n-tooltip :trigger="'hover'">
          <template #trigger>
            <n-button quaternary circle size="small" @click="toggleTheme">
              <template #icon>
                <n-icon size="18">
                  <MoonOutline v-if="!isDark" />
                  <SunnyOutline v-else />
                </n-icon>
              </template>
            </n-button>
          </template>
          <span>{{ t('profile.theme') }}</span>
        </n-tooltip>
        <n-tooltip :trigger="'hover'">
          <template #trigger>
            <n-popover trigger="click" placement="right">
              <template #trigger>
                <n-button quaternary circle size="small">
                  <template #icon>
                    <n-icon size="18"><GlobeOutline /></n-icon>
                  </template>
                </n-button>
              </template>
              <div class="flex flex-col gap-1">
                <n-button
                  v-for="lang in languageOptions"
                  :key="lang.value"
                  text
                  size="small"
                  :type="currentLang === lang.value ? 'primary' : 'default'"
                  @click="switchLanguage(lang.value)"
                >
                  {{ lang.label }}
                  <n-tooltip v-if="lang.beta" trigger="hover" :delay="200" :content-style="{ maxWidth: '260px', width: 'max-content' }">
                    <template #trigger>
                      <n-tag size="tiny" :bordered="false" round type="warning" class="ml-1">Beta</n-tag>
                    </template>
                    {{ t('profile.languageBeta') }}
                  </n-tooltip>
                </n-button>
              </div>
            </n-popover>
          </template>
          <span>{{ t('profile.language') }}</span>
        </n-tooltip>
      </div>

      <!-- 用户信息（点击弹出用户菜单：个人设置 / 系统帮助 / 关于 / 退出登录） -->
      <div class="p-4 border-t border-gray-200 dark:border-gray-800 overflow-hidden">
        <n-dropdown
          trigger="click"
          placement="top-start"
          :options="userMenuOptions"
          @select="handleUserMenuSelect"
        >
          <div class="flex items-center space-x-3 cursor-pointer">
            <n-avatar
              round
              :size="32"
              :src="userInfo?.avatar_url || userInfo?.avatar || '/avatar.png'"
            >
              {{ userInfo?.username?.charAt(0).toUpperCase() }}
            </n-avatar>
            <div v-if="!appStore.sidebarCollapsed" class="flex-1 min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                {{ userInfo?.username }}
              </p>
            </div>
          </div>
        </n-dropdown>
      </div>
    </div>
    
    <!-- 主内容区 -->
    <div class="flex-1 flex flex-col bg-gray-50 dark:bg-gray-950 transition-colors duration-300">
      <!-- 顶部栏 -->
      <header class="h-14 md:h-16 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between px-4 md:px-6 sticky top-0 z-20">
        <div class="flex items-center gap-3 min-w-0">
          <!-- 移动端汉堡菜单 -->
          <n-button text size="small" class="md:hidden" @click="mobileMenuOpen = !mobileMenuOpen">
            <template #icon>
              <n-icon size="20"><MenuOutline /></n-icon>
            </template>
          </n-button>
          <span class="text-xs font-medium text-gray-400 dark:text-gray-500 hidden sm:inline">DocMind</span>
          <span class="text-gray-300 dark:text-gray-600 hidden sm:inline">/</span>
          <h1 class="text-lg font-semibold text-gray-900 dark:text-gray-100 truncate">
            {{ currentRouteTitle }}
          </h1>
          <!-- B1：组织切换器（顶栏品牌区）。切换后当前页面按新组织重挂载。
               T7：移动端（<640px）入口移入顶栏用户菜单（右侧用户图标下拉）——
               紧凑顶栏形态会被右侧固定宽度搜索区遮挡并挤压标题；本组件保持
               桌面端形态，移动端由 userMenuOptions 里的组织分组承接。 -->
          <OrgSwitcher class="hidden sm:block" />
        </div>
        
        <div class="flex items-center gap-2 md:gap-4 shrink-0">
          <n-input
            ref="searchInputRef"
            v-model:value="searchValue"
            :placeholder="t('menu.search')"
            class="w-40 md:w-56 lg:w-64"
            size="small"
            round
            @keyup.enter="handleGlobalSearch"
          >
            <template #prefix>
              <n-icon class="text-gray-400"><SearchOutline /></n-icon>
            </template>
            <template #suffix>
              <span class="text-[10px] text-gray-400 pointer-events-none">Ctrl+K</span>
            </template>
          </n-input>
          
          <n-button text size="small" @click="toggleTheme" class="text-gray-500">
            <n-icon size="18">
              <MoonOutline v-if="!isDark" />
              <SunnyOutline v-else />
            </n-icon>
          </n-button>
          <n-popover trigger="click" placement="bottom-end">
            <template #trigger>
              <n-badge
                :value="unreadCount"
                :max="99"
                :dot="!!notificationStore.lastError"
                :show="!!notificationStore.lastError || unreadCount > 0"
              >
                <n-button text>
                  <n-icon size="18">
                    <NotificationsOutline />
                  </n-icon>
                </n-button>
              </n-badge>
            </template>
            <div class="w-80">
              <div class="flex items-center justify-between mb-2 px-4 pt-2">
                <div class="text-sm font-medium cursor-pointer" @click="router.push({ name: 'Notifications' })">
                  {{ t('menu.notifications') }}
                </div>
                <n-button text size="tiny" type="primary" @click="handleMarkAllRead" v-if="unreadCount > 0">
                  {{ t('notifications.markAllRead') }}
                </n-button>
              </div>
              <!-- 错误态入口（T1 · §7.2③）：拉取失败不再假装"没有通知"，给重试 -->
              <div
                v-if="notificationStore.lastError"
                class="mx-4 mb-2 flex items-center justify-between rounded-md bg-red-50 px-3 py-2 dark:bg-red-500/10"
              >
                <span class="text-xs text-red-500">{{ t('notifications.loadFailed') }}</span>
                <n-button text size="tiny" type="primary" :loading="notificationRetrying" @click="handleNotificationRetry">
                  {{ t('notifications.retry') }}
                </n-button>
              </div>
              <n-list hoverable clickable>
                <n-list-item v-for="notification in notifications" :key="notification.id" @click="handleNotificationClick(notification)">
                  <div class="flex gap-3 px-2 w-full">
                    <div class="mt-1">
                      <div class="w-2 h-2 rounded-full bg-red-500" v-if="!notification.is_read"></div>
                    </div>
                    <div class="flex-1">
                      <div class="font-medium text-sm text-gray-800 dark:text-gray-200">{{ notification.title }}</div>
                      <div class="text-xs text-gray-500 mt-1 line-clamp-2">{{ notification.content }}</div>
                      <div class="text-xs text-gray-400 mt-1">{{ formatDate(notification.created_at) }}</div>
                    </div>
                    <n-button text size="tiny" @click.stop="handleDeleteNotification(notification.id)">删除</n-button>
                  </div>
                </n-list-item>
                <div v-if="notifications.length === 0 && !notificationStore.lastError" class="py-8 text-center text-gray-400 text-sm">
                  {{ t('notifications.empty') }}
                </div>
              </n-list>
            </div>
          </n-popover>
          
          <!-- 用户菜单 -->
          <n-dropdown
            trigger="click"
            :options="userMenuOptions"
            @select="handleUserMenuSelect"
          >
            <n-button text>
              <n-icon size="18">
                <PersonCircleOutline />
              </n-icon>
            </n-button>
          </n-dropdown>
        </div>
      </header>
      
      <!-- 页面内容 -->
      <main class="flex-1 overflow-auto bg-gray-50 dark:bg-gray-950 transition-colors duration-300">
        <router-view v-slot="{ Component, route }">
          <Transition name="page-slide" mode="out-in">
            <!-- E11-③：key 用 route.name（而非 fullPath）——同一路由仅 query
                 变化（chat 切会话、prompt/q 消费、agent 回放深链）不再整页
                 卸载重挂，消除白屏闪烁与输入丢失；跨路由仍按 name 切换重挂。
                 未命名路由回退 path（不存在 query 重复挂载需求）。
                 B1：追加组织上下文版本号——切换组织后当前页面重挂载，
                 文档/会话等 onMounted 拉取按新组织重新发起。 -->
            <div :key="`${String(route.name ?? route.path)}::org-${userStore.organizationVersion}`" class="page-wrapper">
              <Suspense>
                <component :is="Component" />
                <template #fallback>
                  <div class="flex items-center justify-center h-full">
                    <n-spin size="large" />
                  </div>
                </template>
              </Suspense>
            </div>
          </Transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, h, onMounted, onUnmounted, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMediaQuery } from '@vueuse/core'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { markAsRead, markAllAsRead, deleteNotification, type Notification } from '@/api/notification'
import { notificationSocket, type RealtimeNotificationPayload } from '@/utils/notificationSocket'
import { useNotificationStore } from '@/stores/notification'
import { formatDate } from '@/utils/format'
import type { MenuOption, MenuGroupOption, DropdownOption, DropdownGroupOption, DropdownDividerOption } from 'naive-ui'
import { NMenu, NIcon, NInput, NButton, NBadge, NPopover, NList, NListItem, NDropdown, NAvatar } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import { SIDEBAR_MENU_GROUPS, USER_MENU_ITEMS, type MenuItemDef } from '@/config/menu'
import OrgSwitcher from '@/components/layout/OrgSwitcher.vue'
import { setLanguage } from '@/locales'
import { LANGUAGES, isLanguageCode, type LanguageCode } from '@/utils/languages'
import {
  ChatbubbleEllipsesOutline,
  TimeOutline,
  CalendarOutline,
  BookOutline,
  PeopleOutline,
  PersonOutline,
  LogOutOutline,
  SettingsOutline,
  PulseOutline,
  ServerOutline,
  SearchOutline,
  MoonOutline,
  SunnyOutline,
  NotificationsOutline,
  PersonCircleOutline,
  ChevronBackOutline,
  ChevronForwardOutline,
  TerminalOutline,
  GitNetworkOutline,
  BusinessOutline,
  HomeOutline,
  DocumentTextOutline,
  InformationCircleOutline,
  HelpCircleOutline,
  HardwareChipOutline,
  MenuOutline,
  GlobeOutline,
} from '@vicons/ionicons5'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const appStore = useAppStore()
const userStore = useUserStore()

const searchValue = ref('')
const mobileMenuOpen = ref(false)

// T7：移动端断点与样式口径一致（sm = 640px）。用户菜单的组织切换分组只在
// <640px 注入——桌面端仍由顶栏 OrgSwitcher 承担，避免重复入口。
const isMobileViewport = useMediaQuery('(max-width: 639.98px)')

// Language options：单一事实源 utils/languages.ts（beta 字段驱动徽标展示）
const languageOptions = LANGUAGES

const currentLang = computed(() => locale.value)

// 语言包按需加载（E5）：先就绪词条包再切 locale——切换瞬间不裸键；
// 动态 chunk 加载失败时保持当前语言（下次切换重试），不落半截状态。
const switchLanguage = async (lang: LanguageCode) => {
  try {
    await setLanguage(lang)
    userStore.updateSettings({ language: lang })
  } catch {
    // 词条包加载失败：保持当前语言
  }
}

const userInfo = computed(() => userStore.userInfo)
const isDark = computed(() => appStore.isDark)

const notificationStore = useNotificationStore()
const notifications = computed(() => notificationStore.headerItems)
const unreadCount = computed(() => notificationStore.unreadCount)


const navigateByNotification = async (notification: Notification) => {
  const routeName = notification.target_route as string | undefined
  const targetId = notification.target_id as string | number | undefined

  if (routeName) {
    await router.push({ name: routeName as any, query: targetId ? { id: String(targetId) } : undefined })
    return
  }

  const nType = notification.type || 'system'
  if (nType === 'document' || nType === 'knowledge') {
    await router.push({ name: 'Knowledge' })
  } else if (nType === 'chat') {
    await router.push({ name: 'Chat' })
  } else if (nType === 'account' || nType === 'security') {
    await router.push({ name: 'Profile' })
  } else if (nType === 'agent') {
    // Agent 任务终态通知（无 target_route 的旧行兜底）：落到 /agent
    await router.push({ name: 'Agent' })
  } else {
    await router.push({ name: 'Notifications' })
  }
}

const handleNotificationClick = async (notification: Notification) => {
  if (!notification.is_read) {
    try {
      await markAsRead(notification.id)
      notificationStore.consumeRead(notification.id)
    } catch {
      // Failed to mark notification as read
    }
  }

  await navigateByNotification(notification as Notification)
}

const handleMarkAllRead = async () => {
  try {
    await markAllAsRead()
    await notificationStore.bootstrap()
  } catch {
    // Failed to mark all as read
  }
}

// T1 · §7.2③：加载失败后的重试入口（失败已不再置空，重试成功即以服务端真值恢复）
const notificationRetrying = ref(false)
const handleNotificationRetry = async () => {
  notificationRetrying.value = true
  try {
    await notificationStore.bootstrap()
  } finally {
    notificationRetrying.value = false
  }
}

const handleDeleteNotification = async (notificationId: number) => {
  try {
    await deleteNotification(notificationId)
    notificationStore.headerItems = notificationStore.headerItems.filter((n) => n.id !== notificationId)
    await notificationStore.bootstrap()
  } catch {
    // Failed to delete notification
  }
}

const searchInputRef = ref<HTMLElement | null>(null)

const handleKeydown = (e: KeyboardEvent) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault()
    searchInputRef.value?.focus()
  }
}

const handleGlobalSearch = () => {
  if (!searchValue.value.trim()) return
  router.push({
    name: 'Search',
    query: { q: searchValue.value }
  })
  searchValue.value = ''
}

const handleRealtimeNotification = async (payload: RealtimeNotificationPayload) => {
  notificationStore.applyRealtime(payload)
}

onMounted(async () => {
  if (userStore.token) {
    // T7：组织列表由顶栏切换器与移动端用户菜单共享（store 内并发去重）。
    // 先同步发起，避免被下方 await 拆到两个请求窗口。
    void userStore.fetchMyOrganizations()
    await notificationStore.bootstrap()
    notificationSocket.connect()
    // Load user settings from backend
    userStore.fetchSettings().then(() => {
      // Apply theme from settings
      const theme = userStore.settings.theme
      if (theme && ['light', 'dark', 'auto'].includes(theme)) {
        appStore.setTheme(theme as 'light' | 'dark' | 'auto')
      }
      // Apply language from settings（守卫：非法语码不注入 locale；
      // E5：非默认语言包为动态 chunk，异步就绪后再切，失败保持当前语言）
      const lang = userStore.settings.language
      if (isLanguageCode(lang)) {
        void setLanguage(lang).catch(() => {
          // 词条包加载失败：保持当前语言
        })
      }
    })
  }

  notificationSocket.onNotification(handleRealtimeNotification)
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  notificationSocket.offNotification()
  notificationSocket.disconnect()
  window.removeEventListener('keydown', handleKeydown)
})

const currentRouteTitle = computed(() => {
  const metaTitle = route.meta?.title as string
  if (!metaTitle) return t('menu.systemTitle')
  return t(metaTitle)
})
// T9 定位三件套：生效高亮键 = meta.activeMenu ?? route.name。
// 隐藏页（图谱/编辑器/记忆/通知/帮助/关于）显式声明 activeMenu 归属其菜单面，
// 侧栏不再"进去就灭"（修 P1-18 永不高亮）。
const activeKey = computed(() => route.meta.activeMenu ?? (route.name as string))

/** 菜单图标名 → 组件映射（menu.ts 保持纯数据，映射留在布局层） */
const MENU_ICONS: Record<string, Component> = {
  home: HomeOutline,
  chat: ChatbubbleEllipsesOutline,
  history: TimeOutline,
  book: BookOutline,
  terminal: TerminalOutline,
  search: SearchOutline,
  chip: HardwareChipOutline,
  'git-network': GitNetworkOutline,
  calendar: CalendarOutline,
  people: PeopleOutline,
  business: BusinessOutline,
  'document-text': DocumentTextOutline,
  pulse: PulseOutline,
  server: ServerOutline,
  person: PersonOutline,
  settings: SettingsOutline,
  help: HelpCircleOutline,
  info: InformationCircleOutline,
  notifications: NotificationsOutline,
  logout: LogOutOutline,
}

const renderIcon = (Icon: Component) => () => h(NIcon, null, { default: () => h(Icon) })

const toMenuOption = (item: MenuItemDef): MenuOption => ({
  label: t(item.i18nKey),
  key: item.routeName,
  icon: renderIcon(MENU_ICONS[item.icon]),
})

/**
 * G2：侧栏分组标题的折叠/截断策略。
 * 折叠态只有 80px 宽，英文长词（Workspace/Knowledge/Intelligence）没有策略
 * 会被硬裁成 Works/Knowl/Intellig；折叠时不再渲染标题文本（整行由
 * menuNodeProps 收起并标记 aria-hidden）。展开态以 truncate 单行省略，
 * title 悬浮呈现完整词条，不挤压图标列。
 */
const renderGroupLabel = (label: string) => () => {
  if (appStore.sidebarCollapsed) return null
  return h('span', { class: 'block truncate', title: label }, label)
}

const menuNodeProps = (option: MenuOption): Record<string, string> => {
  if ((option as MenuGroupOption).type === 'group' && appStore.sidebarCollapsed) {
    return { class: 'sidebar-group-title--collapsed', 'aria-hidden': 'true' }
  }
  return {}
}

const menuOptions = computed<MenuOption[]>(() => {
  return SIDEBAR_MENU_GROUPS
    .filter((group) => !group.adminOnly || userStore.isAdmin)
    .flatMap((group): MenuOption[] => {
      // W0-1：平台级管理页（监控/审计/MCP）只对 is_superuser 可见；
      // 组织管理页（用户/组织/组织设置）对 isAdmin（平台或 OrgAdmin）可见。
      const items = group.items
        .filter((item) => !item.platformOnly || userStore.isSuperuser)
        .map(toMenuOption)
      return group.groupI18nKey
        ? [{
            type: 'group',
            key: group.groupI18nKey,
            label: renderGroupLabel(t(group.groupI18nKey)),
            children: items,
          }]
        : items
    })
})

// T7：用户菜单可承载组织切换（移动端入口），故选项类型放宽到分组/分割线。
type UserMenuOption = DropdownOption | DropdownGroupOption | DropdownDividerOption

const ORG_SWITCH_PREFIX = 'org-switch:'

/**
 * T7 移动端组织切换入口：<640px 时把组织分组注入既有用户菜单（顶栏右侧
 * 用户图标下拉 + 侧栏用户菜单同源），不占左侧标题宽度。
 * - 与顶栏 OrgSwitcher 共享 userStore.myOrganizations（同一份服务端数据）；
 * - 无可切换目标（单组织且唯一选项即当前组织）时不注入，菜单保持原样；
 * - 超管无主组织（currentOrgId 为 null）时所有选项可选，保留真实切换动作。
 */
const orgMenuOptions = computed<UserMenuOption[]>(() => {
  if (!isMobileViewport.value) return []
  const organizations = userStore.myOrganizations
  if (!organizations.some((org) => org.id !== userStore.currentOrgId)) return []
  return [
    {
      type: 'group',
      key: 'mobile-org-group',
      label: t('org.switchOrganization'),
      children: organizations.map((org) => ({
        label: org.name,
        key: `${ORG_SWITCH_PREFIX}${org.id}`,
        disabled: org.id === userStore.currentOrgId,
        icon: renderIcon(BusinessOutline),
      })),
    },
    { type: 'divider', key: 'mobile-org-divider' },
  ]
})

const userMenuOptions = computed<UserMenuOption[]>(() => [
  ...orgMenuOptions.value,
  ...USER_MENU_ITEMS.map((item) => ({
    label: t(item.i18nKey),
    key: item.actionKey,
    icon: () => h(NIcon, null, { default: () => h(MENU_ICONS[item.icon]) }),
  })),
])

const message = useDedupedMessage()

const handleMenuSelect = async (key: string) => {
  mobileMenuOpen.value = false
  try {
    await router.push({ name: key })
  } catch {
    // 页面加载失败
    message.error('页面加载失败，请尝试刷新页面')
  }
}

// T7：用户菜单里的组织切换动作（key 形如 `org-switch:<id>`）
const switchOrganizationFromMenu = async (orgId: number) => {
  if (orgId === userStore.currentOrgId) return
  try {
    await userStore.switchOrganization(orgId)
    message.success(t('org.switchSuccess'))
  } catch {
    message.error(t('org.switchFailed'))
  }
}

const handleUserMenuSelect = (key: string) => {
  // T7：移动端组织切换项与既有路由/登出动作共用同一下拉
  if (key.startsWith(ORG_SWITCH_PREFIX)) {
    const orgId = Number(key.slice(ORG_SWITCH_PREFIX.length))
    if (Number.isFinite(orgId)) void switchOrganizationFromMenu(orgId)
    return
  }
// T9：用户下拉项 key 与路由键空间统一（actionKey === routeName），
// 处理逻辑从 menu.ts 单一事实源推导（不再硬编码 switch 分支）。
  const item = USER_MENU_ITEMS.find((i) => i.actionKey === key)
  if (!item) return
  if (item.routeName) {
    router.push({ name: item.routeName })
  } else if (item.actionKey === 'logout') {
    // 导航由本处统一 push；logout 只清态与吊销，避免两处异步跳转互相覆盖
    userStore.logout({ navigate: false })
    router.push({ name: 'Login' })
  }
}

const toggleTheme = () => {
  appStore.toggleTheme()
}
</script>

<style scoped>
/* G2：折叠态整行收起分组标题（80px 放不下英文长词；展开态由 label 渲染器 truncate 省略） */
:deep(.n-menu-item-group-title.sidebar-group-title--collapsed) {
  height: 0;
  margin-top: 0;
  overflow: hidden;
}

/* 移动端侧栏由模板上的 max-md:* 工具类承接（fixed/滑入滑出）；
   原先此处的 767px 断点块在模板中无任何引用，属死 CSS，已移除。 */

/* 页面过渡容器 */
.page-wrapper {
  height: 100%;
  min-height: 0;
}
</style>
