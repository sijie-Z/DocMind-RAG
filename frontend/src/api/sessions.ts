/**
 * Session Fork / Lineage API（issue #476 / #499）。
 *
 * - POST /api/v1/sessions/{id}/fork：从 seq <= boundary 前缀分叉子会话（含边界）。
 *   body: {message_id?, title?, include_checkpoint?, seed?, idempotency_key?}
 *   data: {session_id, messages_copied, checkpoint_task_id, id, copied_message_count…}
 * - GET  /api/v1/sessions/{id}/lineage：祖先链（根→父）+ 直接子级 + 谱系元数据。
 *
 * 兼容别名（issue #499，响应带 Deprecation 头）：
 * - POST /api/v1/agent/sessions/{id}/fork
 * - GET  /api/v1/agent/sessions/{id}/lineage
 *
 * 语义（backend/app/services/session_fork_service.py）：
 * - 边界 = seq <= boundary（含边界消息；USER/SYSTEM/ASSISTANT 均可）；
 *   同一轮内的中间 assistant 仍非法；边界消息 pending 时 400。
 * - 源会话有运行中任务时只能显式克隆 checkpoint，否则 409。
 * - 越权/跨组织一律 404（不区分，避免枚举）。
 *
 * 错误体与 commands 同款扁平结构；请求带 X-Silent-Error 由调用方精确呈现。
 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import type { AgentArtifact } from '@/types/artifact'

export interface SessionForkInput {
  message_id?: string
  title?: string
  include_checkpoint?: boolean
  seed?: string
  /** issue #499：幂等键（优先于 Idempotency-Key 头）。 */
  idempotency_key?: string
}

export interface SessionForkData {
  // issue #499 契约字段（顶层；可选以兼容旧后端窗口）
  session_id?: string
  messages_copied?: number
  checkpoint_task_id?: string | null
  // issue #476 兼容字段（保留一版）
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

// ── 批次八：会话树导航 ─────────────────────────────────────────────────────

/** 树中的一个分支节点（与 backend get_session_tree 的 branch payload 对齐）。 */
export interface SessionTreeBranch {
  session_id: string
  title: string | null
  parent_session_id: string | null
  root_session_id: string
  fork_point_message_id: string | null
  /** 切点消息正文前 120 字符（源消息已删除时为空）。 */
  fork_point_preview: string | null
  fork_reason: string | null
  branch_label: string | null
  branch_summary: string | null
  summary_model: string | null
  summarized_at: string | null
  created_at: string
  /** 可见子树内深度（根=0）。 */
  depth: number
  is_root: boolean
  is_current: boolean
  parent_visible: boolean
  message_count: number
  /** 本分支工件行数（含历史版本）。 */
  artifact_count: number
  /** 本分支最新工件版本号（0=暂无工件）。 */
  artifact_latest_version: number
  /** 本分支工件版本号之和（版本链总长）。 */
  artifact_version_total: number
}

export interface SessionTreeData {
  root_session_id: string
  current_session_id: string
  root_present: boolean
  branch_count: number
  branches_truncated: boolean
  tree_truncated: boolean
  branches: SessionTreeBranch[]
}

export interface BranchContextMessage {
  id: string
  seq: number
  content: string
  messageType: 'user' | 'assistant' | 'system'
  createdAt: string
  sources: unknown[]
  feedback: number
  feedbackNote: string | null
}

export interface BranchContextData {
  session_id: string
  title: string | null
  root_session_id: string
  parent_session_id: string | null
  fork_point_message_id: string | null
  fork_reason: string | null
  branch_label: string | null
  branch_summary: string | null
  summary_model: string | null
  summarized_at: string | null
  created_at: string
  updated_at: string | null
  settings: Record<string, unknown> | null
  message_count: number
  artifact_count: number
  artifacts_truncated: boolean
  messages: BranchContextMessage[]
  artifacts: AgentArtifact[]
}

export interface BranchLabelData {
  session_id: string
  branch_label: string | null
  updated: boolean
}

export type BranchSummaryMode = 'fake' | 'llm'

export interface BranchSummaryData {
  session_id: string
  generated: boolean
  summary: string | null
  summary_model: string | null
  summarized_at: string | null
  message_count: number
}

export interface SessionTreeShareData {
  root_session_id: string
  token: string
  url: string
  createdAt: string
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

  /** 批次八：会话树（根 + 可见分支、切点、标签/摘要、按分支工件统计）。只读。 */
  tree(sessionId: string): Promise<AxiosResponse<ApiResponse<SessionTreeData>>> {
    return request.get(`/sessions/${sessionId}/tree`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 选中分支的消息 + 摘要 + 工件上下文；原位切换，不创建会话。 */
  branchContext(
    sessionId: string,
  ): Promise<AxiosResponse<ApiResponse<BranchContextData>>> {
    return request.get(`/sessions/${sessionId}/branch-context`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 设置/清除分支标签（null/空白清除）。 */
  setBranchLabel(
    sessionId: string,
    branchLabel: string | null,
  ): Promise<AxiosResponse<ApiResponse<BranchLabelData>>> {
    return request.put(
      `/sessions/${sessionId}/branch-label`,
      { branch_label: branchLabel },
      { headers: { 'X-Silent-Error': '1' } },
    )
  },

  /** 生成分支摘要；llm 不可用时后端 503 SUMMARY_MODEL_UNAVAILABLE。 */
  summarize(
    sessionId: string,
    mode: BranchSummaryMode = 'llm',
  ): Promise<AxiosResponse<ApiResponse<BranchSummaryData>>> {
    return request.post(
      `/sessions/${sessionId}/summary`,
      { mode },
      { headers: { 'X-Silent-Error': '1' } },
    )
  },

  /** 复制只读分享链接（复用既有分享 token；每次调用轮换）。 */
  shareTree(
    rootSessionId: string,
  ): Promise<AxiosResponse<ApiResponse<SessionTreeShareData>>> {
    return request.post(`/sessions/tree/${rootSessionId}/share`, null, {
      headers: { 'X-Silent-Error': '1' },
    })
  },
}
