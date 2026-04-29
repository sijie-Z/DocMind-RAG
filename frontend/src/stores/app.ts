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
  const systemDarkMode = ref(window.matchMedia('(prefers-color-scheme: dark)').matches)
  
  // 监听系统主题变化
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    systemDarkMode.value = e.matches
  })
  
  // 计算属性：实际生效的主题
  const isDark = computed(() => {
    if (themeMode.value === 'auto') {
      return systemDarkMode.value
    }
    return themeMode.value === 'dark'
  })
  
  const theme = computed(() => isDark.value ? 'dark' : 'light')
  
  // 方法
  const setTheme = (mode: ThemeMode) => {
    themeMode.value = mode
    localStorage.setItem('theme', mode)
    applyTheme()
    
    // 如果已登录，同步到后端
    const userStore = useUserStore()
    if (userStore.token) {
        updateUserProfile({ preferences: { theme: mode } }).catch(err => {
            console.error('Failed to sync theme preference:', err)
        })
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
