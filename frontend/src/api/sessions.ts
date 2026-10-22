/**
 * Session Fork / Lineage API（issue #476）。
 *
 * - POST /api/v1/sessions/{id}/fork：从已结算边界分叉子会话。
 *   body: {message_id?, title?, include_checkpoint?, seed?}
 *   data: {id, copied_message_count, boundary_seq, is_seeded, checkpoint…}
 * - GET  /api/v1/sessions/{id}/lineage：祖先链（根→父）+ 直接子级 + 谱系元数据。
 *
 * 语义（backend/app/services/session_fork_service.py）：
 * - 边界只能落在已结算轮次：ASSISTANT 必须是轮次最终消息；USER 表示“在该消息
 *   之前分叉”；SYSTEM 非法；源会话有运行中任务时只能显式克隆 checkpoint，否则 409。
 * - 越权/跨组织一律 404（不区分，避免枚举）。
 *
 * 错误体与 commands 同款扁平结构；请求带 X-Silent-Error 由调用方精确呈现。
 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface SessionForkInput {
  message_id?: string
  title?: string
  include_checkpoint?: boolean
  seed?: string
}

export interface SessionForkData {
  id: string
  title: string
  parent_session_id: string | null
  root_session_id: string
  fork_point_message_id: string | null
  fork_reason: string | null
  message_count: number
  copied_message_count: number
  boundary_seq: number
  is_seeded: boolean
  checkpoint: {
    cloned: boolean
    task_id: string | null
    source_task_id: string | null
  }
  idempotent_replay: boolean
}

export interface LineageNode {
  id: string
  title: string | null
  parent_session_id: string | null
  fork_point_message_id: string | null
  fork_reason: string | null
  is_fork: boolean
  created_at: string
  message_count?: number
  is_seeded?: boolean
}

export interface SessionLineageData {
  session_id: string
  title: string | null
  root_session_id: string | null
  parent_session_id: string | null
  fork_point_message_id: string | null
  fork_reason: string | null
  is_fork: boolean
  is_seeded: boolean
  depth: number
  ancestors: LineageNode[]
  children: LineageNode[]
  parent_missing: boolean
  lineage_truncated: boolean
}

export const sessionsApi = {
  /** 从边界消息分叉；省略 message_id = 从最后一条已结算消息分叉。 */
  fork(
    sessionId: string,
    input: SessionForkInput = {},
  ): Promise<AxiosResponse<ApiResponse<SessionForkData>>> {
    return request.post(`/sessions/${sessionId}/fork`, input, {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 会话谱系：祖先链 + 直接子级（父会话删除后仍可查）。 */
  lineage(sessionId: string): Promise<AxiosResponse<ApiResponse<SessionLineageData>>> {
    return request.get(`/sessions/${sessionId}/lineage`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },
}
