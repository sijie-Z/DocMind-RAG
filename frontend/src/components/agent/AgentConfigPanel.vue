<template>
  <div class="config-panel">
    <div class="config-header" @click="collapsed = !collapsed">
      <span class="config-title">⚙ Agent 配置</span>
      <span class="config-toggle">{{ collapsed ? '◀' : '▼' }}</span>
    </div>

    <div v-if="!collapsed" class="config-body">
      <!-- Model -->
      <div class="config-section">
        <label class="config-label">模型</label>
        <select v-model="localConfig.model" class="config-select">
          <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
          <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
        </select>
      </div>

      <!-- Temperature -->
      <div class="config-section">
        <label class="config-label">
          温度: <span class="config-value">{{ localConfig.temperature }}</span>
        </label>
        <input
          type="range"
          v-model.number="localConfig.temperature"
          min="0"
          max="2"
          step="0.05"
          class="config-slider"
        />
      </div>

      <!-- Toggles -->
      <div class="config-toggles">
        <div class="toggle-row">
          <span class="toggle-label">📋 规划</span>
          <label class="switch">
            <input type="checkbox" v-model="localConfig.enable_planning" />
            <span class="switch-slider"></span>
          </label>
        </div>
        <div class="toggle-row">
          <span class="toggle-label">🔍 反思</span>
          <label class="switch">
            <input type="checkbox" v-model="localConfig.enable_reflection" />
            <span class="switch-slider"></span>
          </label>
        </div>
        <div class="toggle-row">
          <span class="toggle-label">🧠 记忆</span>
          <label class="switch">
            <input type="checkbox" v-model="localConfig.enable_memory" />
            <span class="switch-slider"></span>
          </label>
        </div>
        <div class="toggle-row">
          <span class="toggle-label">💭 思考流</span>
          <label class="switch">
            <input type="checkbox" v-model="localConfig.enable_thinking" />
            <span class="switch-slider"></span>
          </label>
        </div>
      </div>

      <!-- Personality -->
      <div class="config-section">
        <label class="config-label">风格</label>
        <select v-model="localConfig.personality" class="config-select">
          <option value="balanced">平衡</option>
          <option value="precise">精准</option>
          <option value="creative">创意</option>
        </select>
      </div>

      <!-- Tools -->
      <div class="config-section">
        <label class="config-label">工具开关</label>
        <div class="tool-list">
          <div v-for="tool in availableTools" :key="tool.name" class="tool-item">
            <label class="switch small">
              <input
                type="checkbox"
                :checked="!localConfig.disabled_tools.includes(tool.name)"
                @change="toggleTool(tool.name)"
              />
              <span class="switch-slider"></span>
            </label>
            <span class="tool-item-name" :title="tool.description">
              {{ tool.name }}
              <span v-if="tool.disabled_by_default" class="default-disabled">默认关闭</span>
            </span>
          </div>
        </div>
      </div>

      <!-- System prompt -->
      <div class="config-section">
        <label class="config-label">系统提示词</label>
        <textarea
          v-model="localConfig.system_prompt_override"
          class="config-textarea"
          placeholder="留空使用默认提示词..."
          rows="3"
        ></textarea>
      </div>

      <!-- Apply button -->
      <button class="apply-btn" @click="$emit('apply', localConfig)">
        应用配置
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import type { AgentConfig, ToolInfo } from '@/types/agent'
import { agentApi } from '@/api/agent'

const props = defineProps<{
  config: AgentConfig
}>()

const emit = defineEmits<{
  apply: [config: AgentConfig]
}>()

const collapsed = ref(true)
const availableTools = ref<ToolInfo[]>([])

const localConfig = reactive<AgentConfig>({ ...props.config })

watch(
  () => props.config,
  (c) => Object.assign(localConfig, c),
  { deep: true },
)

function toggleTool(name: string) {
  const idx = localConfig.disabled_tools.indexOf(name)
  if (idx === -1) {
    localConfig.disabled_tools.push(name)
  } else {
    localConfig.disabled_tools.splice(idx, 1)
  }
}

onMounted(async () => {
  try {
    const res = await agentApi.listTools()
    const tools = res.data
    if (Array.isArray(tools)) availableTools.value = tools
  } catch {
    // ignore
  }
})
</script>

<style scoped>
.config-panel {
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 12px;
  overflow: hidden;
  font-size: 12px;
}

.dark .config-panel {
  border-color: var(--border-color-dark, #374151);
}

.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: var(--bg-secondary, #f9fafb);
  cursor: pointer;
  user-select: none;
}

.dark .config-header {
  background: var(--bg-secondary-dark, #111827);
}

.config-title {
  font-weight: 600;
  color: var(--text-primary, #111827);
}

.dark .config-title {
  color: var(--text-primary-dark, #f9fafb);
}

.config-toggle { color: var(--text-tertiary, #9ca3af); font-size: 10px; }

.config-body {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 600px;
  overflow-y: auto;
}

.config-section { display: flex; flex-direction: column; gap: 4px; }

.config-label {
  font-weight: 500;
  color: var(--text-secondary, #4b5563);
  display: flex;
  align-items: center;
  gap: 4px;
}

.dark .config-label {
  color: var(--text-secondary-dark, #9ca3af);
}

.config-value { color: #3b82f6; font-weight: 600; }

.config-select {
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 12px;
}

.dark .config-select {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.config-slider {
  width: 100%;
  accent-color: #3b82f6;
}

.config-toggles { display: flex; flex-direction: column; gap: 8px; }

.toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle-label { color: var(--text-secondary, #4b5563); }
.dark .toggle-label { color: var(--text-secondary-dark, #9ca3af); }

/* Switch */
.switch {
  position: relative;
  display: inline-block;
  width: 36px;
  height: 20px;
}

.switch.small { width: 28px; height: 16px; }

.switch input { opacity: 0; width: 0; height: 0; }

.switch-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background: #d1d5db;
  border-radius: 20px;
  transition: 0.2s;
}

.switch-slider::before {
  content: '';
  position: absolute;
  height: 16px; width: 16px;
  left: 2px; bottom: 2px;
  background: white;
  border-radius: 50%;
  transition: 0.2s;
}

.switch.small .switch-slider::before {
  height: 12px; width: 12px;
}

input:checked + .switch-slider { background: #3b82f6; }
input:checked + .switch-slider::before {
  transform: translateX(16px);
}
.switch.small input:checked + .switch-slider::before {
  transform: translateX(12px);
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}

.tool-item-name {
  font-size: 11px;
  font-family: monospace;
  color: var(--text-secondary, #4b5563);
}

.dark .tool-item-name {
  color: var(--text-secondary-dark, #9ca3af);
}

.default-disabled {
  font-size: 9px;
  color: #f59e0b;
  margin-left: 4px;
}

.config-textarea {
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #d1d5db);
  font-size: 11px;
  resize: vertical;
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-family: inherit;
}

.dark .config-textarea {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.apply-btn {
  padding: 8px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
}

.apply-btn:hover { background: #2563eb; }
</style>
