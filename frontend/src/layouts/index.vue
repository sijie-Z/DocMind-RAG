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
      <!-- Logo：DocMind 图标 + 名称 -->
      <div class="h-16 flex items-center px-4 border-b border-gray-100 dark:border-gray-800/50 bg-white/50 dark:bg-gray-900/50 backdrop-blur-sm overflow-hidden whitespace-nowrap">
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
                </n-button>
              </div>
            </n-popover>
          </template>
          <span>{{ t('profile.language') }}</span>
        </n-tooltip>
      </div>

      <!-- 用户信息 -->
      <div class="p-4 border-t border-gray-200 dark:border-gray-800 overflow-hidden">
        <div class="flex items-center space-x-3">
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
            <n-tag
              v-if="currentOrgId"
              size="tiny"
              round
              :bordered="false"
              type="info"
              class="mt-0.5"
            >
              <template #icon>
                <n-icon size="10"><BusinessOutline /></n-icon>
              </template>
              组织 {{ currentOrgId }}
            </n-tag>
          </div>
        </div>
      </div>
    </div>
    
    <!-- 主内容区 -->
    <div class="flex-1 flex flex-col bg-gray-50 dark:bg-gray-950 transition-colors duration-300">
      <!-- 顶部栏 -->
      <header class="h-14 md:h-16 bg-white dark:bg-gray-900 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between px-4 md:px-6 sticky top-0 z-20">
        <div class="flex items-center gap-3 min-w-0">
          <!-- 移动端汉堡菜单 -->
          <n-button text size="small" class="md:hidden" @click="mobileMenuOpen = !mobileMenuOpen">
            <template #icon>
              <n-icon size="20"><MenuOutline /></n-icon>
            </template>
          </n-button>
          <span class="text-xs font-medium text-slate-400 dark:text-slate-500 hidden sm:inline">DocMind</span>
          <span class="text-slate-300 dark:text-slate-600 hidden sm:inline">/</span>
          <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-100 truncate">
            {{ currentRouteTitle }}
          </h1>
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
              <n-icon class="text-slate-400"><SearchOutline /></n-icon>
            </template>
            <template #suffix>
              <span class="text-[10px] text-slate-400 pointer-events-none">Ctrl+K</span>
            </template>
          </n-input>
          
          <n-button text size="small" @click="toggleTheme" class="text-slate-500">
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
                :show="unreadCount > 0"
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
                  全部已读
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
                <div v-if="notifications.length === 0" class="py-8 text-center text-gray-400 text-sm">
                  暂无通知
                </div>
              </n-list>
            </div>
          </n-popover>
          
          <!-- 用户菜单 -->
          <n-dropdown
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
            <div :key="route.fullPath" class="page-wrapper">
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
import { computed, ref, h, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { markAsRead, markAllAsRead, deleteNotification, type Notification } from '@/api/notification'
import { notificationSocket, type RealtimeNotificationPayload } from '@/utils/notificationSocket'
import { useNotificationStore } from '@/stores/notification'
import { formatDate } from '@/utils/format'
import type { MenuOption, DropdownOption } from 'naive-ui'
import { NMenu, NIcon, NInput, NButton, NBadge, NPopover, NList, NListItem, NDropdown, NAvatar } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import {
  ChatbubbleEllipsesOutline,
  TimeOutline,
  BookOutline,
  PeopleOutline,
  PersonOutline,
  LogOutOutline,
  SettingsOutline,
  PulseOutline,
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

// Language options
const languageOptions = [
  { label: '简体中文', value: 'zh' },
  { label: 'English', value: 'en' },
  { label: '日本語', value: 'ja' },
  { label: 'Français', value: 'fr' },
]

const currentLang = computed(() => locale.value)

const switchLanguage = (lang: string) => {
  locale.value = lang
  localStorage.setItem('language', lang)
  userStore.updateSettings({ language: lang })
}

const userInfo = computed(() => userStore.userInfo)
const currentOrgId = computed(() => userStore.currentOrgId)
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
    await notificationStore.bootstrap()
    notificationSocket.connect()
    // Load user settings from backend
    userStore.fetchSettings().then(() => {
      // Apply theme from settings
      const theme = userStore.settings.theme
      if (theme && ['light', 'dark', 'auto'].includes(theme)) {
        appStore.setTheme(theme as 'light' | 'dark' | 'auto')
      }
      // Apply language from settings
      const lang = userStore.settings.language
      if (lang) {
        locale.value = lang
        localStorage.setItem('language', lang)
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
const activeKey = computed(() => route.name as string)

const menuOptions = computed<MenuOption[]>(() => {
  const isAdmin = userStore.isAdmin
  const options: MenuOption[] = [
    {
      label: t('menu.home'),
      key: 'Dashboard',
      icon: () => h(NIcon, null, { default: () => h(HomeOutline) })
    },
    {
      label: t('menu.chat'),
      key: 'Chat',
      icon: () => h(NIcon, null, { default: () => h(ChatbubbleEllipsesOutline) })
    },
    {
      label: t('menu.conversations'),
      key: 'Conversations',
      icon: () => h(NIcon, null, { default: () => h(TimeOutline) })
    },
    {
      label: t('menu.knowledge'),
      key: 'Knowledge',
      icon: () => h(NIcon, null, { default: () => h(BookOutline) })
    },
    {
      label: t('menu.prompts'),
      key: 'Prompts',
      icon: () => h(NIcon, null, { default: () => h(TerminalOutline) })
    },
    {
      label: t('menu.search'),
      key: 'Search',
      icon: () => h(NIcon, null, { default: () => h(SearchOutline) })
    },
    {
      label: t('menu.users'),
      key: 'Users',
      icon: () => h(NIcon, null, { default: () => h(PeopleOutline) }),
      show: isAdmin
    },
    {
      label: t('menu.organizations'),
      key: 'Organizations',
      icon: () => h(NIcon, null, { default: () => h(BusinessOutline) }),
      show: isAdmin
    },
    {
      label: t('menu.auditLogs'),
      key: 'AuditLogs',
      icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) }),
      show: isAdmin
    },
    {
      label: t('menu.monitoring'),
      key: 'Monitoring',
      icon: () => h(NIcon, null, { default: () => h(PulseOutline) }),
      show: isAdmin
    },
    {
      label: t('menu.personalSettings'),
      key: 'Profile',
      icon: () => h(NIcon, null, { default: () => h(PersonOutline) })
    },
    {
      label: t('menu.workflow'),
      key: 'Workflow',
      icon: () => h(NIcon, null, { default: () => h(GitNetworkOutline) })
    },
    {
      label: t('menu.agent'),
      key: 'Agent',
      icon: () => h(NIcon, null, { default: () => h(HardwareChipOutline) })
    }
  ]
  return options.filter(item => item.show !== false)
})

const userMenuOptions = computed<DropdownOption[]>(() => [
  {
    label: t('menu.personalSettings'),
    key: 'settings',
    icon: () => h(NIcon, null, { default: () => h(SettingsOutline) })
  },
  {
    label: t('menu.systemHelp'),
    key: 'system-help',
    icon: () => h(NIcon, null, { default: () => h(HelpCircleOutline) })
  },
  {
    label: t('menu.systemAbout'),
    key: 'system-about',
    icon: () => h(NIcon, null, { default: () => h(InformationCircleOutline) })
  },
  {
    label: t('menu.logout'),
    key: 'logout',
    icon: () => h(NIcon, null, { default: () => h(LogOutOutline) })
  }
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

const handleUserMenuSelect = (key: string) => {
  switch (key) {
    case 'settings':
      router.push({ name: 'Profile' })
      break
    case 'system-help':
      router.push({ name: 'SystemHelp' })
      break
    case 'system-about':
      router.push({ name: 'SystemAbout' })
      break
    case 'logout':
      userStore.logout()
      router.push({ name: 'Login' })
      break
  }
}

const toggleTheme = () => {
  appStore.toggleTheme()
}
</script>

<style scoped>
/* Mobile sidebar: fixed overlay on small screens */
@media (max-width: 767px) {
  .sidebar-fixed {
    position: fixed;
    inset: 0;
    z-index: 50;
  }
}

/* 页面过渡容器 */
.page-wrapper {
  height: 100%;
  min-height: 0;
}
</style>
