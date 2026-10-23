/**
 * User Commands API（issue #475）。
 *
 * 两个入口语义（以 backend/app/api/v1/endpoints/commands.py 为准）：
 * - GET  /api/v1/commands：当前用户可用命令；携带 session_id 时合并该会话的
 *   agent 作用域命令。响应 data = {enabled, scope, commands[]}；
 *   COMMANDS_ENABLED=false 时 enabled=false 且 commands=[]（前端回退本地模板）。
 * - POST /api/v1/commands/execute：执行一行命令（body: {line, session_id?}），
 *   返回 JSON，**不进入聊天流**。成功 data = CommandExecution.to_dict()；
 *   失败为扁平错误体 {success:false, error_code, message, detail}。
 *
 * 错误由调用方自行呈现（X-Silent-Error: 1），避免全局 toast 覆盖字段级原因。
 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface CommandArgInfo {
  name: string
  description: string
  required: boolean
  choices: string[]
  default: string | null
}

export interface CommandInfo {
  name: string
  aliases: string[]
  description: string
  /** 后端随命令下发的 zh/en 文案（前端按 locale 取用，避免双语漂移） */
  description_i18n?: Record<string, string>
  usage: string
  permission: 'user' | 'admin'
  args: CommandArgInfo[]
}

export interface CommandListData {
  enabled: boolean
  scope: string
  commands: CommandInfo[]
}

/** POST /commands/execute 成功返回（与 backend CommandExecution.to_dict 逐字段对齐）。 */
export interface CommandExecutionData {
  command: string
  line: string
  scope: string
  success: boolean
  content: string
  data: Record<string, any>
  args: Record<string, any>
  error_code: string
  error_message: string
  error_field: string
  duration_ms: number
}

export const commandsApi = {
  /** 列出可用命令（按当前会话的 agent 作用域合并）。 */
  list(sessionId?: string | null): Promise<AxiosResponse<ApiResponse<CommandListData>>> {
    return request.get('/commands', {
      params: sessionId ? { session_id: sessionId } : undefined,
    })
  },

  /** 执行一行命令（不写用户消息、不触发模型）。 */
  execute(
    line: string,
    sessionId?: string | null,
  ): Promise<AxiosResponse<ApiResponse<CommandExecutionData>>> {
    return request.post(
      '/commands/execute',
      { line, session_id: sessionId || undefined },
      { headers: { 'X-Silent-Error': '1' } },
    )
  },
}
