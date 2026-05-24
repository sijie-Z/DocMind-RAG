import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  AgentConfig,
  AgentChatMessage,
  AgentEvent,
  AgentSession,
  PlanStep,
  ToolInfo,
} from '@/types/agent'

export const useAgentStore = defineStore('agent', () => {
  // ── Config ──
  const config = ref<AgentConfig>({
    model: 'deepseek-v4-flash',
    temperature: 0.1,
    max_tokens: 4096,
    enable_planning: true,
    enable_reflection: true,
    enable_tools: true,
    enable_memory: true,
    enable_thinking: true,
    personality: 'balanced',
    disabled_tools: ['execute_python', 'execute_sql'],
    max_plan_steps: 10,
    max_retries_per_step: 3,
  })

  // ── Sessions ──
  const sessions = ref<AgentSession[]>([])
  const currentSessionId = ref<string | null>(null)

  // ── Runtime state ──
  const messages = ref<AgentChatMessage[]>([])
  const isLoading = ref(false)
  const isThinking = ref(false)
  const currentToolName = ref('')
  const planSteps = ref<PlanStep[]>([])
  const planId = ref('')
  const overallProgress = ref(0)
  const tools = ref<ToolInfo[]>([])
  const expandedEvents = ref<Set<string>>(new Set())

  // ── Computed ──
  const toolCount = computed(() => tools.value.length)
  const enabledToolCount = computed(
    () => tools.value.filter((t) => !config.value.disabled_tools.includes(t.name)).length,
  )
  const hasSessions = computed(() => sessions.value.length > 0)
  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentSessionId.value),
  )

  // ── Actions ──

  function addMessage(msg: AgentChatMessage) {
    messages.value.push(msg)
  }

  function updateLastAssistant(updater: (msg: AgentChatMessage) => void) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) updater(last)
  }

  function processEvent(event: AgentEvent) {
    switch (event.type) {
      case 'thinking':
        isThinking.value = true
        updateLastAssistant((msg) => {
          if (event.thinking_type === 'correction') {
            msg.thinkingText += '\n' + event.content
          } else {
            msg.thinkingText += event.content
          }
        })
        break

      case 'plan_start':
        planId.value = event.plan_id || ''
        planSteps.value = []
        updateLastAssistant((msg) => {
          msg.planId = event.plan_id || ''
          msg.planSteps = []
        })
        break

      case 'plan_step':
        if (event.plan_step_id) {
          const step: PlanStep = {
            id: event.plan_step_id,
            description: event.content,
            dependencies: event.dependencies || [],
            tool_hint: event.tool_hint || null,
            status: 'pending',
            retry_count: 0,
          }
          planSteps.value.push(step)
          updateLastAssistant((msg) => {
            msg.planSteps = [...planSteps.value]
          })
        }
        break

      case 'plan_complete':
        isThinking.value = false
        break

      case 'tool_call':
        currentToolName.value = event.tool_name || ''
        updateLastAssistant((msg) => {
          msg.currentTool = event.tool_name || ''
          msg.events.push(event)
        })
        // Update plan step status
        if (event.plan_step_id) {
          const step = planSteps.value.find((s) => s.id === event.plan_step_id)
          if (step) step.status = 'running'
        }
        break

      case 'tool_result':
        currentToolName.value = ''
        updateLastAssistant((msg) => {
          msg.currentTool = ''
          msg.events.push(event)
        })
        if (event.plan_step_id) {
          const step = planSteps.value.find((s) => s.id === event.plan_step_id)
          if (step && step.status !== 'failed') step.status = 'completed'
        }
        if (event.plan_progress !== undefined) {
          overallProgress.value = event.plan_progress
        }
        break

      case 'tool_error':
        currentToolName.value = ''
        updateLastAssistant((msg) => {
          msg.currentTool = ''
          msg.events.push(event)
        })
        if (event.plan_step_id) {
          const step = planSteps.value.find((s) => s.id === event.plan_step_id)
          if (step) step.status = 'failed'
        }
        break

      case 'reflection':
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        overallProgress.value = 1.0
        break

      case 'chunk':
        updateLastAssistant((msg) => {
          msg.content += event.content
        })
        break

      case 'error':
        updateLastAssistant((msg) => {
          msg.events.push(event)
          msg.loading = false
        })
        isLoading.value = false
        break

      case 'done':
        updateLastAssistant((msg) => {
          msg.loading = false
          if (event.message_id) {
            msg.messageId = event.message_id
          }
        })
        isLoading.value = false
        isThinking.value = false
        overallProgress.value = 1.0
        break
    }
  }

  function clearMessages() {
    messages.value = []
    planSteps.value = []
    planId.value = ''
    overallProgress.value = 0
    isThinking.value = false
    currentToolName.value = ''
    expandedEvents.value = new Set()
  }

  function setLoading(val: boolean) {
    isLoading.value = val
  }

  function setSessions(list: AgentSession[]) {
    sessions.value = list
  }

  function setCurrentSession(id: string | null) {
    currentSessionId.value = id
  }

  function updateConfig(partial: Partial<AgentConfig>) {
    Object.assign(config.value, partial)
  }

  function toggleEventExpanded(eventKey: string) {
    const next = new Set(expandedEvents.value)
    if (next.has(eventKey)) {
      next.delete(eventKey)
    } else {
      next.add(eventKey)
    }
    expandedEvents.value = next
  }

  return {
    // State
    config,
    sessions,
    currentSessionId,
    messages,
    isLoading,
    isThinking,
    currentToolName,
    planSteps,
    planId,
    overallProgress,
    tools,
    expandedEvents,
    // Computed
    toolCount,
    enabledToolCount,
    hasSessions,
    currentSession,
    // Actions
    addMessage,
    updateLastAssistant,
    processEvent,
    clearMessages,
    setLoading,
    setSessions,
    setCurrentSession,
    updateConfig,
    toggleEventExpanded,
  }
})
