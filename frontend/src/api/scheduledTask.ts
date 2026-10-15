/**
 * 定时任务 API（/api/v1/scheduled-tasks）。
 *
 * 契约要点（对齐后端 app/api/v1/endpoints/scheduled_tasks.py）：
 * - 列表/创建路径带尾斜杠（后端声明在 "/"）；列表/创建返回裸 schema
 *   （不走 success/data 包装），列表为 {items, total}；
 * - 创建 200；语义校验失败 400（cron 无解 / once_at 过期 / 时区非法）；
 *   无组织 403（require_organization fail-closed）；schema 校验 422；
 * - 归属：非创建者（且非超管）一律 404；resume 已完成单次任务 → 409；
 * - interval_seconds 下限 60（与后端 schema 同源常量）。
 *
 * 写操作带 X-Silent-Error：403/409/400 的诚实文案由调用方按原样呈现
 * （拦截器 403 通用文案会盖掉"未加入组织"等准确语义）。
 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

export type ScheduleType = 'cron' | 'interval' | 'once'
export type ScheduleRunStatus = 'running' | 'success' | 'failed'

/** 与后端 schemas/scheduled_task.py MIN_INTERVAL_SECONDS 同值。 */
export const MIN_INTERVAL_SECONDS = 60
export const MAX_NAME_LENGTH = 200
export const MAX_PROMPT_LENGTH = 20000
export const MAX_CRON_LENGTH = 100
export const MAX_TIMEZONE_LENGTH = 64

export interface ScheduledTask {
  id: string
  name: string
  prompt: string
  schedule_type: ScheduleType
  cron_expr: string | null
  interval_seconds: number | null
  once_at: string | null
  timezone: string
  enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_status: ScheduleRunStatus | null
  last_task_id: string | null
  last_error: string | null
  run_count: number
  skipped_overlap_count: number
  created_by: number
  organization_id: number | null
  created_at: string | null
  updated_at: string | null
}

export interface ScheduledTaskListData {
  items: ScheduledTask[]
  total: number
}

export interface ScheduledTaskCreatePayload {
  name: string
  prompt: string
  schedule_type: ScheduleType
  cron_expr?: string | null
  interval_seconds?: number | null
  once_at?: string | null
  timezone?: string | null
  enabled?: boolean
}

export interface RunNowResult {
  triggered: boolean
  reason: string
}

const SILENT = { headers: { 'X-Silent-Error': '1' } }

export const listScheduledTasks = (params?: {
  enabled?: boolean
  skip?: number
  limit?: number
}): Promise<AxiosResponse<ScheduledTaskListData>> => request.get('/scheduled-tasks/', { params })

export const createScheduledTask = (
  payload: ScheduledTaskCreatePayload,
): Promise<AxiosResponse<ScheduledTask>> => request.post('/scheduled-tasks/', payload, SILENT)

export const pauseScheduledTask = (id: string): Promise<AxiosResponse<ScheduledTask>> =>
  request.post(`/scheduled-tasks/${id}/pause`, null, SILENT)

export const resumeScheduledTask = (id: string): Promise<AxiosResponse<ScheduledTask>> =>
  request.post(`/scheduled-tasks/${id}/resume`, null, SILENT)

export const runNowScheduledTask = (id: string): Promise<AxiosResponse<RunNowResult>> =>
  request.post(`/scheduled-tasks/${id}/run-now`, null, SILENT)

export const deleteScheduledTask = (id: string): Promise<AxiosResponse<{ success: boolean }>> =>
  request.delete(`/scheduled-tasks/${id}`, SILENT)
