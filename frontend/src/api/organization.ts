import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface Organization {
  id: number
  name: string
  color: string
  description?: string
  parent_id?: number
  level: number
  sort_order: number
  created_at: string
  updated_at: string
  children?: Organization[]
}

export interface OrganizationListResponse {
  data: Organization[]
  total: number
  page: number
  page_size: number
}

export const getOrganizations = async (params?: {
  page?: number
  page_size?: number
  search?: string
}): Promise<AxiosResponse<ApiResponse<OrganizationListResponse>>> => {
  return request.get('/organizations/', {
    params,
    headers: {
      'X-Silent-Error': '1'
    }
  })
}

export const getOrganization = async (id: number): Promise<AxiosResponse<{ data: Organization }>> => {
  return request.get(`/organizations/${id}`)
}

export const createOrganization = async (data: {
  name: string
  color: string
  description?: string
  parent_id?: number
}): Promise<AxiosResponse<{ data: Organization }>> => {
  // 🛑 关键修复：添加末尾斜杠 /
  return request.post('/organizations/', data)
}

export const updateOrganization = async (id: number, data: {
  name?: string
  color?: string
  description?: string
  parent_id?: number
  sort_order?: number
}): Promise<AxiosResponse<{ data: Organization }>> => {
  return request.put(`/organizations/${id}`, data)
}

export const deleteOrganization = async (id: number): Promise<void> => {
  return request.delete(`/organizations/${id}`)
}

export const batchDeleteOrganizations = async (ids: number[]): Promise<void> => {
  return request.delete('/organizations/batch', { data: { ids } })
}

export const getOrganizationTree = async (): Promise<AxiosResponse<{ data: Organization[] }>> => {
  return request.get('/organizations/tree', {
    headers: {
      'X-Silent-Error': '1'
    }
  })
}

export const getOrganizationMembers = async (id: number): Promise<AxiosResponse<{ data: any[] }>> => {
  return request.get(`/organizations/${id}/members`)
}

export const getOrganizationDocuments = async (id: number): Promise<AxiosResponse<{ data: any[] }>> => {
  return request.get(`/organizations/${id}/documents`)
}
