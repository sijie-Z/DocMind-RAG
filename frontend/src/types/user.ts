export interface User {
  id: number
  username: string
  email: string
  fullName?: string
  full_name?: string // Backend field
  nickname?: string // Frontend display name
  phone?: string
  isActive: boolean
  isAdmin: boolean
  role?: string // 'admin' | 'user'
  organization_id?: number
  createdAt: string
  updatedAt: string
  organizations: Organization[]
}

export interface UserInfo {
  id: number
  username: string
  email: string
  fullName?: string
  full_name?: string // Backend field
  nickname?: string
  phone?: string
  isAdmin: boolean
  role?: string
  organization_id?: number
  createdAt: string
  organizations: Organization[]
  avatar?: string
  avatar_url?: string // Backend field
  bio?: string
  preferences?: string | any // JSON string or object
}

export interface LoginForm {
  username: string
  password: string
}

export interface RegisterForm {
  username: string
  email: string
  password: string
  fullName?: string
}

export interface Organization {
  id: number
  name: string
  description?: string
  isDefault: boolean
  createdAt: string
  createdBy: number
}
