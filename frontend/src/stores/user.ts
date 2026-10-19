import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { loginWithJson, register as apiRegister, getUserProfile, logout as apiLogout } from '@/api/auth'
import { setToken, setRefreshToken, removeToken, getToken, getRefreshToken } from '@/utils/auth'
import type { UserInfo, LoginForm, RegisterForm } from '@/types/user'
import type { AxiosError } from 'axios'
import { useAppStore } from './app'
import { getUserSettings, updateUserSettings, getMyOrganizations, switchOrganization as switchOrganizationApi, type UserSettings } from '@/api/user'

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
  // 组织上下文版本号：每次成功切换组织递增。布局层把它并入页面 key，
  // 使当前页面重挂载，文档/会话等 onMounted 拉取按新组织重新发起。
  const organizationVersion = ref(0)
  // 服务可用性标记：/users/me 遇 5xx/网络错误时为 true（保留会话，供全局横幅/不可用页消费）
  const authServiceUnavailable = ref(false)

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
  // admin 判定与后端鉴权同口径（is_superuser or role == "admin"）：
  // 菜单过滤（layouts）与路由守卫（router）都消费这同一个计算属性。
  const isAdmin = computed(
    () => userInfo.value?.role === 'admin' || userInfo.value?.is_superuser === true
  )
  const currentOrgId = computed(() => currentOrganizationId.value || userInfo.value?.organization_id || null)

  const setCurrentOrganization = (orgId: number) => {
    currentOrganizationId.value = orgId
  }

  /**
   * 切换当前组织（B1）：先由服务端校验成员关系并落库，成功后才更新本地上下文。
   * 服务端失败时本地状态与版本号保持不变——不制造“前端已切换、服务端未切换”的假成功。
   * 切到当前组织是幂等空操作，不产生请求。
   */
  const switchOrganization = async (orgId: number) => {
    if (orgId === currentOrgId.value) {
      return { success: true as const, organizationId: orgId }
    }
    const response = await switchOrganizationApi(orgId)
    const resData = response.data as unknown as Record<string, unknown>
    const payload = (
      resData.data && typeof resData.data === 'object' ? resData.data : resData
    ) as Record<string, unknown>
    const appliedOrgId = Number(payload.organization_id ?? orgId)
    currentOrganizationId.value = appliedOrgId
    if (userInfo.value) {
      userInfo.value = { ...userInfo.value, organization_id: appliedOrgId }
    }
    organizationVersion.value += 1
    return { success: true as const, organizationId: appliedOrgId }
  }

  /**
   * 当前用户可切换的组织列表（成员关系 + 主组织兜底）。
   * 同时对齐服务端返回的 `current_organization_id`：多设备/多标签页下
   * 当前组织以服务端为准，避免本地标签短暂漂移。
   * 拉取失败时返回空数组，由切换器据当前组织状态决定可见性。
   *
   * T7：列表提升为共享状态 `myOrganizations`——顶栏组织切换器与移动端
   * 用户菜单同源消费；布局层与切换器同 tick 挂载时，并发请求共享同一次
   * GET（去重），不重复打接口。
   */
  const myOrganizations = ref<Array<{ id: number; name: string }>>([])
  let organizationsRequest: Promise<Array<{ id: number; name: string }>> | null = null

  const fetchMyOrganizations = async () => {
    if (organizationsRequest) return organizationsRequest
    const request = (async () => {
      try {
        const response = await getMyOrganizations()
        const resData = response.data as unknown as Record<string, unknown>
        const payload = (
          resData.data && typeof resData.data === 'object' ? resData.data : resData
        ) as Record<string, unknown>
        const organizations = Array.isArray(payload.organizations) ? payload.organizations : []
        const serverOrgId = payload.current_organization_id
        if (typeof serverOrgId === 'number' && serverOrgId !== currentOrgId.value) {
          currentOrganizationId.value = serverOrgId
          if (userInfo.value) {
            userInfo.value = { ...userInfo.value, organization_id: serverOrgId }
          }
        }
        myOrganizations.value = organizations as Array<{ id: number; name: string }>
        return organizations as Array<{ id: number; name: string }>
      } catch {
        myOrganizations.value = []
        return [] as Array<{ id: number; name: string }>
      } finally {
        organizationsRequest = null
      }
    })()
    organizationsRequest = request
    return request
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
      authServiceUnavailable.value = false
      return response.data
    } catch (error) {
      // 仅 401（或拦截器已判定续期失败的标记错误）视为会话失效；
      // 5xx / 网络错误保留 token 与本地缓存的 userInfo，交由 UI 提示“服务不可用”。
      const status = (error as AxiosError)?.response?.status
      const sessionExpired =
        status === 401 || (error as { authSessionExpired?: boolean })?.authSessionExpired === true
      if (sessionExpired) {
        authServiceUnavailable.value = false
        // 不在此处导航：调用方（路由守卫/聊天页）自行决定落点；同步清本地即可
        logout({ navigate: false })
      } else {
        authServiceUnavailable.value = true
      }
      throw error
    }
  }

  /** 同步清空本地登录态（不请求服务端、不导航）：供登出与路由守卫的导航链路复用 */
  const clearAuthState = () => {
    userInfo.value = null
    token.value = ''
    authServiceUnavailable.value = false
    removeToken()
    // 清除用户态 localStorage，避免登出后残留敏感/偏好数据
    localStorage.removeItem('user_info')
    localStorage.removeItem('language')
    localStorage.removeItem('theme')
    localStorage.removeItem('activeSystemPrompt')
  }

  /**
   * 登出：同步清本地 → （默认）跳登录页 → 服务端吊销令牌。
   * 服务端失败不阻断本地登出（console.warn 告警，吊销幂等可稍后重试）。
   * `navigate: false`：路由守卫等自行调用 next() 的导航链路使用，
   * 避免本方法内的异步跳转覆盖其 redirect 语义。
   */
  const logout = async (options?: { navigate?: boolean }) => {
    const accessToken = getToken()
    const refreshToken = getRefreshToken()
    // 先同步收口本地登录态：导航链路（如守卫）在 await 之前即可观察到已登出
    clearAuthState()
    // 安全加固：清除 axios 默认 Authorization 头，防止登出后旧 token 残留
    // （动态导入避免与 request.ts 的静态循环依赖）
    try {
      const { clearDefaultAuth } = await import('@/utils/request')
      clearDefaultAuth()
    } catch { /* request 模块未就绪时忽略 */ }
    // 跳转登录页；已在登录页时跳过（调用方自行跳转时传 navigate: false）
    // 动态导入避免与 router/index.ts（其静态依赖本 store）形成初始化循环。
    if (options?.navigate !== false) {
      try {
        const { default: router } = await import('@/router')
        if (router.currentRoute.value.name !== 'Login') {
          await router.push({ name: 'Login' })
        }
      } catch { /* router 未就绪时忽略 */ }
    }
    // 服务端吊销：本地已清空，显式携带此前捕获的 access token
    if (accessToken) {
      try {
        await apiLogout(refreshToken, accessToken)
      } catch (error) {
        console.warn('[logout] server-side revoke failed; local credentials cleared', error)
      }
    }
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
    organizationVersion,
    myOrganizations,
    settings,
    authServiceUnavailable,

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
    switchOrganization,
    fetchMyOrganizations,
    fetchSettings,
    updateSettings,
  }
})
