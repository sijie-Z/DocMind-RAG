import request from '@/utils/request'
import type { LoginForm, RegisterForm, UserInfo } from '@/types/user'
import type { ApiResponse } from '@/types/common'

// 用户登录（默认使用 Form 格式，符合 OAuth2 标准）
export function login(data: LoginForm) {
  const formData = new URLSearchParams()
  formData.append('username', data.username)
  formData.append('password', data.password)

  return request.post<ApiResponse<{
    access_token: string
    token_type: string
    expires_in: number
    refresh_token: string
    user_info: UserInfo
  }>>('/auth/login', formData, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  })
}

/**
 * 使用 JSON 体的登录（与后端 /auth/login/json 对应）。
 * 若 Form 登录一直 500，可在 stores/user.ts 中临时改为 loginWithJson 测试：
 * 若 JSON 也 500，说明是数据库/后端逻辑问题；若 JSON 成功，则可能是 Form 解析问题。
 */
export function loginWithJson(data: LoginForm) {
  return request.post<ApiResponse<{
    access_token: string
    token_type: string
    expires_in: number
    refresh_token: string
    user_info: UserInfo
  }>>('/auth/login/json', data)
}

// 用户注册 (注册通常是普通的 JSON，一般不需要改，除非你后端注册也写成了表单)
export function register(data: RegisterForm) {
  return request.post<ApiResponse>(
    '/auth/register',
    data
  )
}

/**
 * 用户登出：请求服务端吊销当前 access token（可选附带 refresh token，
 * 防止登出后仍可用 refresh 续期）。
 *
 * `accessToken`：调用方通常在发请求前已同步清空本地凭证，请求拦截器不再自动
 * 注入 Authorization，这里显式携带以完成吊销。
 *
 * `_skipAuthRefresh`：本请求收到 401 时不得再进入刷新/自动登出链，
 * 否则「登出 → 401 → 刷新失败 → 自动登出 → 登出」会互相递归。
 * `X-Silent-Error`：失败提示由调用方（user store）统一处理，
 * 避免拦截器再弹一条全局错误 toast。
 */
export function logout(refreshToken?: string, accessToken?: string) {
  const headers: Record<string, string> = { 'X-Silent-Error': '1' }
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }
  const config = {
    headers,
    _skipAuthRefresh: true,
  }
  return request.post<ApiResponse>(
    '/auth/logout',
    refreshToken ? { refresh_token: refreshToken } : null,
    config
  )
}

// 获取用户信息
export function getUserProfile() {
  // 使用 /users/me 接口，因为它包含更完整的用户信息处理（如 bio/preferences 解析）
  return request.get<ApiResponse<UserInfo>>(
    '/users/me'
  )
}
