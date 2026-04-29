import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

export interface Manual {
  id: number
  title: string
  content: string
  category: string
  sort_order: number
  is_published: boolean
  created_at: string
  updated_at: string
}

export interface ManualCreate {
  title: string
  content: string
  category?: string
  sort_order?: number
  is_published?: boolean
}

export interface ManualUpdate {
  title?: string
  content?: string
  category?: string
  sort_order?: number
  is_published?: boolean
}

export const getManuals = async (category?: string): Promise<AxiosResponse<Manual[]>> => {
  return request.get('/manuals/', { params: { category } })
}

export const getManual = async (id: number): Promise<AxiosResponse<Manual>> => {
  return request.get(`/manuals/${id}`)
}

export const createManual = async (data: ManualCreate): Promise<AxiosResponse<Manual>> => {
  return request.post('/manuals/', data)
}

export const updateManual = async (id: number, data: ManualUpdate): Promise<AxiosResponse<Manual>> => {
  return request.put(`/manuals/${id}`, data)
}

export const deleteManual = async (id: number): Promise<AxiosResponse<any>> => {
  return request.delete(`/manuals/${id}`)
}
