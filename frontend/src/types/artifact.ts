/**
 * Agent 工件（批次七）：版本化产出物的前端类型真源。
 *
 * 与后端契约逐字对齐：
 * - 行投影：`backend/app/services/artifact_service.py::artifact_to_dict`
 * - 列表/详情信封：`GET /api/v1/artifacts`（{success, data:{items,total}}）
 * - SSE `artifact` 事件载荷：`backend/app/agent/events.py`（artifact_* 字段）
 */

/** 工件类型（与后端 ARTIFACT_KINDS 同集）。 */
export type ArtifactKind = 'text' | 'markdown' | 'json' | 'diff' | 'code' | 'html' | 'svg'

/** 工件行（列表/详情同一口径；content_url 由服务端附加）。 */
export interface AgentArtifact {
  id: string
  organization_id: number
  user_id: number
  conversation_id?: string | null
  task_id?: string | null
  agent_id?: string | null
  kind: ArtifactKind
  title: string
  filename?: string | null
  mime_type: string
  content_sha256: string
  file_size: number
  source_tool?: string | null
  source_call_id?: string | null
  version_no: number
  replaces_artifact_id?: string | null
  status: string
  created_at?: string | null
  content_url?: string
  /** 详情端点的完整版本链（旧 → 新）；列表项没有该字段。 */
  versions?: AgentArtifact[]
}

export interface ArtifactListResponse {
  items: AgentArtifact[]
  total: number
}

/** SSE `artifact` 事件（后端 AgentEvent 的 artifact_* 字段的 camelCase 投影）。 */
export interface ArtifactStreamEvent {
  type: 'artifact'
  content: string
  artifactId: string
  title: string
  kind: ArtifactKind
  version: number
  sourceTool?: string
  origin: 'tool' | 'manual' | string
  created?: boolean
  contentUrl: string
  toolCallId?: string
  planStepId?: string
}
