/**
 * Agent 事件展示文案（issue #289）：
 * 从 views/agent/index.vue 抽出，供 Agent 页与聊天页（Agent 模式）共用，
 * 保证同一 run 两个入口对 retry_hint 的展示口径一致。
 */

// retry_hint 的展示文案（issue #250 第 6 条收尾）：后端 content 形如
// "retry_action=reset_transport"（executor.py 发射），直接显示原始串对
// 用户不友好；映射成中文动作说明，未知动作回退原文。
const RETRY_ACTION_LABELS: Record<string, string> = {
  reset_transport: '连接中断，正在重建连接后重试',
  refresh_auth: '凭据失效，正在刷新凭据后重试',
}

export function formatRetryHint(content: string | undefined): string {
  const action = (content || '').replace('retry_action=', '')
  return RETRY_ACTION_LABELS[action] || content || '正在重试'
}
