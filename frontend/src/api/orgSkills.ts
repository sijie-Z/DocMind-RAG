/**
 * 组织技能库 API 客户端（批次十）。
 *
 * 产品口径（与后端 `org_skills.py` / `org_skill_service.py` 一致）：
 * - 技能是**评审门控的组织资产**：提议只写 pending 版本；运行时只注入
 *   approved + enabled + 哈希校验通过 + 工具仍可见的版本；
 * - payload 是结构化数据（触发短语 + 工具名序列），不含参数/自由指令；
 *   `description` 仅用于 UI，不进入 system prompt；
 * - 审核/启用/禁用/回滚/归档是组织管理员动作（后端 `require_org_admin` +
 *   JWT），作者不能审核自己的版本。
 */
import request from '@/utils/request'

export type OrgSkillStatus = 'active' | 'archived'
export type OrgSkillReviewStatus = 'pending' | 'approved' | 'rejected'
export type OrgSkillSource = 'run_proposal' | 'manual'

export interface OrgSkillToolStep {
  tool_name: string
}

export interface OrgSkillPayload {
  schema_version: 1
  trigger_phrases: string[]
  tool_sequence: OrgSkillToolStep[]
}

export interface OrgSkillSummary {
  id: string
  organization_id: number
  name: string
  description: string | null
  status: OrgSkillStatus
  latest_version: number
  enabled_version_no: number | null
  created_by: number
  updated_by: number | null
  archived_at: string | null
  archived_by: number | null
  lock_version: number
  created_at: string | null
  updated_at: string | null
  pending_count?: number
}

export interface OrgSkillVersion {
  id: string
  skill_id: string
  version_no: number
  payload_sha256: string
  source: OrgSkillSource
  provenance: Record<string, unknown> | null
  review_status: OrgSkillReviewStatus
  reviewed_by: number | null
  reviewed_at: string | null
  review_note: string | null
  created_by: number
  created_at: string | null
  payload?: OrgSkillPayload
}

export interface OrgSkillDetail extends OrgSkillSummary {
  versions: OrgSkillVersion[]
}

export interface OrgSkillListData {
  items: OrgSkillSummary[]
  total: number
  page: number
  page_size: number
}

export interface OrgSkillDiff {
  skill_id: string
  base_version_no: number
  target_version_no: number
  base_sha256: string
  target_sha256: string
  trigger_phrases: {
    base: string[]
    target: string[]
    added: string[]
    removed: string[]
  }
  tool_sequence: {
    base: string[]
    target: string[]
    added: string[]
    removed: string[]
  }
}

export interface OrgSkillProposalPayload {
  name?: string
  description?: string | null
  skill_id?: string
  payload?: OrgSkillPayload
  source_task_id?: string
}

export interface OrgSkillProposalResult {
  skill: OrgSkillSummary
  version: OrgSkillVersion
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } }

export const orgSkillsApi = {
  /** 列表（默认 active；`all` 可含已归档） */
  list(params?: {
    status?: OrgSkillStatus | 'all'
    q?: string
    page?: number
    page_size?: number
  }) {
    return request.get('/org-skills', { params })
  },

  /** 技能详情（含全部不可变版本） */
  get(skillId: string) {
    return request.get(`/org-skills/${encodeURIComponent(skillId)}`)
  },

  /** 单个版本（含结构化 payload） */
  getVersion(skillId: string, versionNo: number) {
    return request.get(`/org-skills/${encodeURIComponent(skillId)}/versions/${versionNo}`)
  },

  /** 结构化 diff（触发短语/工具序列增删） */
  diff(skillId: string, baseVersion: number, targetVersion: number) {
    return request.get(`/org-skills/${encodeURIComponent(skillId)}/diff`, {
      params: { base_version_no: baseVersion, target_version_no: targetVersion },
    })
  },

  /** 提议 pending 版本（手工 payload 或从本人完成的任务派生） */
  propose(payload: OrgSkillProposalPayload) {
    return request.post('/org-skills/proposals', payload, SILENT_HEADERS)
  },

  /** 批准 pending 版本（组织管理员；作者不可自审） */
  approve(skillId: string, versionNo: number, note?: string | null) {
    return request.post(
      `/org-skills/${encodeURIComponent(skillId)}/versions/${versionNo}/approve`,
      { note: note ?? null },
      SILENT_HEADERS,
    )
  },

  /** 拒绝 pending 版本（组织管理员；作者不可自审） */
  reject(skillId: string, versionNo: number, note?: string | null) {
    return request.post(
      `/org-skills/${encodeURIComponent(skillId)}/versions/${versionNo}/reject`,
      { note: note ?? null },
      SILENT_HEADERS,
    )
  },

  /** 启用 approved 版本（组织管理员 + JWT） */
  enable(skillId: string, versionNo: number) {
    return request.post(
      `/org-skills/${encodeURIComponent(skillId)}/enable`,
      { version_no: versionNo },
      SILENT_HEADERS,
    )
  },

  /** 关闭运行时注入（组织管理员 + JWT） */
  disable(skillId: string) {
    return request.post(`/org-skills/${encodeURIComponent(skillId)}/disable`, undefined, SILENT_HEADERS)
  },

  /** 回滚到更早的 approved 版本（缺省 = 当前版本前最近的批准版本） */
  rollback(skillId: string, versionNo?: number) {
    return request.post(
      `/org-skills/${encodeURIComponent(skillId)}/rollback`,
      versionNo ? { version_no: versionNo } : {},
      SILENT_HEADERS,
    )
  },

  /** 归档（只读终态，不删除任何行） */
  archive(skillId: string) {
    return request.post(`/org-skills/${encodeURIComponent(skillId)}/archive`, undefined, SILENT_HEADERS)
  },
}
