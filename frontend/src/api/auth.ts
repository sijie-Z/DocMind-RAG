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

// 获取用户信息
export function getUserProfile() {
  // 使用 /users/me 接口，因为它包含更完整的用户信息处理（如 bio/preferences 解析）
  return request.get<ApiResponse<UserInfo>>(
    '/users/me'
  )
}

// 刷新访问令牌（后端 /auth/refresh 使用 query 参数）
export function refreshAccessToken(refreshToken: string) {
  return request.post<ApiResponse<{
    access_token: string
    token_type: string
    expires_in: number
    refresh_token: string
    user_info: UserInfo
  }>>('/auth/refresh', null, {
    params: { refresh_token: refreshToken },
    headers: { 'X-Silent-Error': '1' }
  })
}
