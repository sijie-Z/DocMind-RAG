import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { loginWithJson, register as apiRegister, getUserProfile } from '@/api/auth'
import { setToken, setRefreshToken, removeToken, getToken } from '@/utils/auth'
import type { UserInfo, LoginForm, RegisterForm } from '@/types/user'
import type { AxiosError } from 'axios'
import { useAppStore } from './app'
import { getUserSettings, updateUserSettings, type UserSettings } from '@/api/user'

function _extractErrorMessage(error: unknown, fallback: string): string {
  const axiosErr = error as AxiosError<{ message?: string; detail?: string }>
  return (
    axiosErr.response?.data?.message ??
    axiosErr.response?.data?.detail ??
    (error instanceof Error ? error.message : null) ??
    fallback
  )
}

export const useUserStore = defineStore('user', () => {
  // 状态
  function _loadUserInfo(): UserInfo | null {
    try {
      const raw = localStorage.getItem('user_info')
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  }
  const userInfo = ref<UserInfo | null>(_loadUserInfo())
  const token = ref<string>(getToken() || '')
  const currentOrganizationId = ref<number | null>(null)

  // User settings state
  const settings = ref<UserSettings>({
    language: localStorage.getItem('language') || 'zh',
    theme: localStorage.getItem('theme') || 'light',
  })

  // 仅在 getUserInfo 验证成功后由 _applyValidatedAuth 统一设置
  const _applyValidatedAuth = (accessToken: string | undefined, info: UserInfo | undefined) => {
    if (accessToken) {
      token.value = accessToken
    }
    if (info) {
      userInfo.value = info
      currentOrganizationId.value = info.organization_id || null
      if (info.preferences) {
        try {
          const prefs = typeof info.preferences === 'string'
            ? JSON.parse(info.preferences)
            : info.preferences
          if (prefs?.theme) {
            useAppStore().setTheme(prefs.theme)
          }
        } catch { /* non-critical */ }
      }
    }
  }
  
  // 监听 userInfo 变化并持久化
  watch(userInfo, (newVal) => {
    if (newVal) {
      localStorage.setItem('user_info', JSON.stringify(newVal))
    } else {
      localStorage.removeItem('user_info')
    }
  }, { deep: true })
  
  // 计算属性
  const isLoggedIn = computed(() => !!token.value && !!userInfo.value)
  const isAdmin = computed(() => userInfo.value?.role === 'admin')
  const currentOrgId = computed(() => currentOrganizationId.value || userInfo.value?.organization_id || null)

  const setCurrentOrganization = (orgId: number) => {
    currentOrganizationId.value = orgId
  }
  
  // 方法
  const login = async (form: LoginForm) => {
    try {
      const response = await loginWithJson(form)
      const raw = response.data as unknown as Record<string, unknown>
      const payload = (raw?.data as Record<string, unknown>) ?? raw
      if (!payload || !payload.access_token) {
        throw new Error((raw?.message as string) || '后端返回数据格式错误')
      }

      const access_token = payload.access_token as string
      const refresh_token = payload.refresh_token as string | undefined
      const expires_in = payload.expires_in as number | undefined
      const loginUserInfo = payload.user_info as UserInfo | undefined

      setToken(access_token, expires_in ?? 86400)
      if (refresh_token) setRefreshToken(refresh_token)

      if (loginUserInfo) {
        _applyValidatedAuth(access_token, loginUserInfo)
      } else {
        token.value = access_token
        await getUserInfo()
      }

      // Auto-fetch user settings after login
      fetchSettings()

      return { success: true }
    } catch (error: unknown) {
      return {
        success: false,
        message: _extractErrorMessage(error, '登录失败')
      }
    }
  }

  const register = async (form: RegisterForm) => {
    try {
      const response = await apiRegister(form)
      const raw = response.data as unknown as Record<string, unknown>
      const payload = (raw?.data as Record<string, unknown>) ?? raw
      if (payload?.access_token) {
        setToken(payload.access_token as string, (payload.expires_in as number) ?? 86400)
        if (payload.refresh_token) setRefreshToken(payload.refresh_token as string)
        _applyValidatedAuth(payload.access_token as string, payload.user_info as UserInfo | undefined)
      }
      return { success: true }
    } catch (error: unknown) {
      return {
        success: false,
        message: _extractErrorMessage(error, '注册失败，请检查输入信息')
      }
    }
  }

  const getUserInfo = async () => {
    // 从 localStorage 直接读 token，不依赖 token.value（避免初始化时为空导致死锁）
    const storedToken = getToken()
    if (!storedToken) {
      return Promise.reject(new Error('No token found'))
    }

    try {
      const response = await getUserProfile()
      const resData = response.data as unknown as Record<string, unknown>
      const info = (resData.data && typeof resData.data === 'object' && !resData.username)
        ? resData.data as UserInfo
        : resData as unknown as UserInfo
      _applyValidatedAuth(storedToken, info)
      return response.data
    } catch (error) {
      logout()
      throw error
    }
  }

  const logout = () => {
    userInfo.value = null
    token.value = ''
    removeToken()
  }
  
  // --- 找到 stores/user.ts 中的 updateUserInfo 函数，替换为 ---
  const updateUserInfo = (info: Partial<UserInfo>) => {
    if (userInfo.value) {
      // ⚡ 核心修复：使用解构赋值确保引用变化，从而触发 watch
      userInfo.value = { ...userInfo.value, ...info }
    }
  }

  // --- User Settings Actions ---

  const fetchSettings = async () => {
    try {
      const response = await getUserSettings()
      const resData = response.data as unknown as Record<string, unknown>
      const data = (resData.data && typeof resData.data === 'object')
        ? resData.data as UserSettings
        : resData as unknown as UserSettings
      if (data.language) {
        settings.value.language = data.language
        localStorage.setItem('language', data.language)
      }
      if (data.theme) {
        settings.value.theme = data.theme
        localStorage.setItem('theme', data.theme)
      }
      if (data.preferences) {
        settings.value.preferences = data.preferences
      }
    } catch {
      // Non-critical — use local settings
    }
  }

  const updateSettings = async (data: { language?: string; theme?: string }) => {
    try {
      const response = await updateUserSettings(data)
      const resData = response.data as unknown as Record<string, unknown>
      const result = (resData.data && typeof resData.data === 'object')
        ? resData.data as UserSettings
        : resData as unknown as UserSettings
      if (result.language) {
        settings.value.language = result.language
        localStorage.setItem('language', result.language)
      }
      if (result.theme) {
        settings.value.theme = result.theme
        localStorage.setItem('theme', result.theme)
      }
      return { success: true as const }
    } catch {
      return { success: false as const }
    }
  }
  
  return {
    // 状态
    userInfo,
    token,
    currentOrganizationId,
    settings,

    // 计算属性
    isLoggedIn,
    isAdmin,
    currentOrgId,

    // 方法
    login,
    register,
    getUserInfo,
    logout,
    updateUserInfo,
    setCurrentOrganization,
    fetchSettings,
    updateSettings,
  }
})
