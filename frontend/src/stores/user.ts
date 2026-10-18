import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { loginWithJson, register as apiRegister, getUserProfile, logout as apiLogout } from '@/api/auth'
import { setToken, setRefreshToken, removeToken, getToken, getRefreshToken } from '@/utils/auth'
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

  const register = async (
    form: RegisterForm
  ): Promise<{ success: true; tokenIssued: boolean } | { success: false; message: string }> => {
    try {
      const response = await apiRegister(form)
      const raw = response.data as unknown as Record<string, unknown>
      // 后端返回形态双兼容：token 可能在 data 包装里，也可能平铺在根上
      const asRecord = (v: unknown): Record<string, unknown> | null =>
        v && typeof v === 'object' ? (v as Record<string, unknown>) : null
      const root = asRecord(raw)
      const wrapped = asRecord(root?.data)
      const accessToken = (wrapped?.access_token ?? root?.access_token ?? wrapped?.token ?? root?.token) as
        | string
        | undefined

      if (accessToken) {
        const expiresIn = (wrapped?.expires_in ?? root?.expires_in ?? 86400) as number
        setToken(accessToken, expiresIn)
        const refreshToken = (wrapped?.refresh_token ?? root?.refresh_token) as string | undefined
        if (refreshToken) setRefreshToken(refreshToken)
        _applyValidatedAuth(accessToken, (wrapped?.user_info ?? root?.user_info) as UserInfo | undefined)
      }
      // tokenIssued 供调用方分流：拿到 token → 按认证后落点跳转；
      // 未拿到（后端未直接发放）→ 回登录页预填用户名并提示
      return { success: true, tokenIssued: !!accessToken }
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

  const logout = async () => {
    const refreshToken = getRefreshToken()
    // 先请求服务端吊销 access/refresh token，避免登出后旧令牌仍可访问受保护接口。
    // 服务端失败（5xx/断网）不阻断本地登出：本地凭证照常清空并告警；
    // 吊销是幂等的，稍后重试登出即可。无本地 access token 时后端必然 401，跳过请求。
    if (getToken()) {
      try {
        await apiLogout(refreshToken)
      } catch (error) {
        console.warn('[user] 服务端登出失败，已继续清除本地凭证', error)
      }
    }
    userInfo.value = null
    token.value = ''
    removeToken()
    // 清除用户态 localStorage，避免登出后残留敏感/偏好数据
    localStorage.removeItem('user_info')
    localStorage.removeItem('language')
    localStorage.removeItem('theme')
    localStorage.removeItem('activeSystemPrompt')
    // 安全加固：清除 axios 默认 Authorization 头，防止登出后旧 token 残留
    // （动态导入避免与 request.ts 的静态循环依赖）
    try {
      const { clearDefaultAuth } = await import('@/utils/request')
      clearDefaultAuth()
    } catch { /* request 模块未就绪时忽略 */ }
    // 跳转登录页；已在登录页时跳过（调用方可能已自行跳转，避免重复导航）
    // 动态导入避免与 router/index.ts（其静态依赖本 store）形成初始化循环。
    try {
      const { default: router } = await import('@/router')
      if (router.currentRoute.value.name !== 'Login') {
        await router.push({ name: 'Login' })
      }
    } catch { /* router 未就绪时忽略 */ }
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

  const updateSettings = async (data: { language?: string; theme?: string; preferences?: Record<string, unknown> }) => {
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
