/**
 * MCP 服务器状态 API（/api/v1/mcp/servers，管理员只读）。
 *
 * 只读视图：启停/增删服务器 = 编辑 backend/mcp_servers.json 后重启（本页不做在线写）。
 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

/** 连接状态（对齐后端 MCPServerConnection._state 值域 + manager 派生值）。 */
export type McpServerState = 'init' | 'connecting' | 'connected' | 'dead' | 'closed' | 'disabled' | 'not_connected'

export interface McpServerStatus {
  name: string
  enabled: boolean
  transport: string
  description: string
  state: McpServerState
  /** 已注册动态工具计数。 */
  tools: number
  /** 已注册工具名（原始名，server 侧命名）。 */
  tool_names: string[]
  /** 最近一次接入/刷新失败原文（成功后清除）。 */
  last_error: string | null
}

export interface McpServersData {
  /** 管理器是否已完成 initialize（lifespan 后为 true）。 */
  started: boolean
  servers: McpServerStatus[]
  registered_tools: number
}

export const getMcpServers = (): Promise<AxiosResponse<ApiResponse<McpServersData>>> =>
  request.get('/mcp/servers')
