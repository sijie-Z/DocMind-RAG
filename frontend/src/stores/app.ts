import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { updateUserProfile } from '@/api/user'
import { useUserStore } from './user' // Need to be careful, but using store instance inside action is safer

export type ThemeMode = 'light' | 'dark' | 'auto'

export const useAppStore = defineStore('app', () => {
  // 状态
  const themeMode = ref<ThemeMode>('auto')
  const sidebarCollapsed = ref(false)
  const loading = ref(false)

  // 系统偏好监听
  const systemDarkMode = ref(typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)

  // 监听系统主题变化
  if (typeof window !== 'undefined') {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      systemDarkMode.value = e.matches
    })
  }

  // 防抖定时器
  let themeSyncTimer: ReturnType<typeof setTimeout> | null = null
  
  // 计算属性：实际生效的主题
  const isDark = computed(() => {
    if (themeMode.value === 'auto') {
      return systemDarkMode.value
    }
    return themeMode.value === 'dark'
  })
  
  const theme = computed(() => isDark.value ? 'dark' : 'light')
  
  // 方法
  const setTheme = (mode: ThemeMode, options: { persist?: boolean } = {}) => {
    const changed = themeMode.value !== mode
    themeMode.value = mode
    localStorage.setItem('theme', mode)
    applyTheme()

    // 同值重设不产生写请求；从服务端水合（persist: false）只应用不回声——
    // 否则每次登录/刷新都会把刚从后端读到的主题再 PUT 回 /users/me。
    if (options.persist === false) {
      // 水合是服务端最新值：撤销尚未发出的显式写，避免随后把已被覆盖的
      // 选择写回后端（UI 与服务端偏好分叉）。
      if (themeSyncTimer) {
        clearTimeout(themeSyncTimer)
        themeSyncTimer = null
      }
      return
    }
    if (!changed) return

    // 如果已登录，防抖同步到后端
    const userStore = useUserStore()
    if (userStore.token) {
      if (themeSyncTimer) clearTimeout(themeSyncTimer)
      themeSyncTimer = setTimeout(() => {
        updateUserProfile({ preferences: { theme: mode } }).catch(() => {
          // Failed to sync theme preference
        })
      }, 500)
    }
  }
  
  const toggleTheme = () => {
    // 简单的切换逻辑：如果当前是暗色（不管是auto还是dark），切换到light；否则切换到dark
    if (isDark.value) {
      setTheme('light')
    } else {
      setTheme('dark')
    }
  }
  
  const applyTheme = () => {
    if (isDark.value) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }
  
  const toggleSidebar = () => {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebarCollapsed', sidebarCollapsed.value.toString())
  }

  const setSidebarCollapsed = (value: boolean) => {
    sidebarCollapsed.value = value
    localStorage.setItem('sidebarCollapsed', value.toString())
  }
  
  const setLoading = (value: boolean) => {
    loading.value = value
  }
  
  // 初始化主题
  const initTheme = () => {
    const savedTheme = localStorage.getItem('theme') as ThemeMode | null
    const savedSidebar = localStorage.getItem('sidebarCollapsed')
    
    if (savedTheme && ['light', 'dark', 'auto'].includes(savedTheme)) {
      themeMode.value = savedTheme
    } else {
      themeMode.value = 'auto'
    }
    
    applyTheme()
    
    if (savedSidebar) {
      sidebarCollapsed.value = savedSidebar === 'true'
    }
  }
  
  // 监听 isDark 变化自动应用 class
  watch(isDark, () => {
    applyTheme()
  })
  
  // 初始化
  initTheme()

  return {
    // 状态
    themeMode,
    isDark,
    sidebarCollapsed,
    loading,
    
    // 计算属性
    theme,
    
    // 方法
    setTheme,
    toggleTheme,
    toggleSidebar,
    setSidebarCollapsed,
    setLoading,
    initTheme
  }
})
