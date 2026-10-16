<template>
  <div class="mcp-servers-container h-full p-4 lg:p-6 overflow-y-auto">
    <div class="bg-white/70 dark:bg-gray-900/70 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 shadow-2xl shadow-blue-500/10 h-full flex flex-col overflow-hidden">
      <!-- 页面头部 -->
      <div class="p-6 border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-gradient-to-r from-slate-50/30 to-slate-50/30 dark:from-slate-950/10 dark:to-slate-950/10">
        <div>
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-blue-600 dark:from-blue-400 dark:to-blue-400">
            {{ t('menu.mcpServers') }}
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {{ t('mcp.subtitle') }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <n-tag v-if="!loadError && data" size="small" round :bordered="false" type="info">
            {{ t('mcp.registeredTools', { n: data.registered_tools }) }}
          </n-tag>
          <n-button
            secondary
            circle
            class="hover:rotate-180 transition-all duration-500"
            @click="loadServers"
          >
            <template #icon>
              <n-icon><refresh-outline /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="flex-1 p-6 overflow-y-auto">
        <n-spin :show="loading" class="h-full">
          <template #description>
            <span>{{ t('common.loading') }}</span>
          </template>

          <div v-if="loadError" class="flex items-center justify-center h-64">
            <n-result status="error" :title="t('mcp.loadFailed')" :description="loadErrorMsg">
              <template #footer>
                <n-button type="primary" round @click="loadServers">{{ t('common.refresh') }}</n-button>
              </template>
            </n-result>
          </div>

          <template v-else-if="data">
            <n-alert class="mb-4" type="info" :show-icon="true" :bordered="false">
              {{ t('mcp.readOnlyNotice') }}
            </n-alert>
            <n-alert v-if="!data.started" class="mb-4" type="warning" :show-icon="true" :bordered="false">
              {{ t('mcp.notStarted') }}
            </n-alert>

            <n-empty v-if="data.servers.length === 0" class="py-12" :description="t('mcp.noServers')">
              <template #extra>
                <span class="text-xs text-gray-400">{{ t('mcp.noServersHint') }}</span>
              </template>
            </n-empty>

            <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              <div
                v-for="srv in data.servers"
                :key="srv.name"
                class="rounded-2xl border border-gray-200/60 dark:border-gray-800/60 bg-white/60 dark:bg-gray-900/40 p-4 flex flex-col gap-3 shadow-sm"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="min-w-0">
                    <div class="font-semibold truncate">{{ srv.name }}</div>
                    <n-tag size="tiny" round :bordered="false" class="mt-1">
                      {{ t('mcp.transportLabel') }}: {{ srv.transport }}
                    </n-tag>
                  </div>
                  <n-tag size="small" round :bordered="false" :type="stateTagType(srv.state)">
                    {{ stateText(srv.state) }}
                  </n-tag>
                </div>

                <div class="text-xs text-gray-500 dark:text-gray-400 min-h-[2rem]">
                  {{ srv.description || '—' }}
                </div>

                <div class="flex items-center gap-2 text-xs">
                  <n-tag size="tiny" round :bordered="false" :type="srv.enabled ? 'success' : 'default'">
                    {{ srv.enabled ? t('mcp.enabled') : t('mcp.disabled') }}
                  </n-tag>
                  <span class="text-gray-500 dark:text-gray-400">
                    {{ t('mcp.toolCount', { n: srv.tools }) }}
                  </span>
                </div>

                <div v-if="srv.tool_names.length > 0" class="flex flex-wrap gap-1">
                  <n-tag v-for="tool in srv.tool_names" :key="tool" size="tiny" round :bordered="false" type="info">
                    {{ tool }}
                  </n-tag>
                </div>
                <div v-else class="text-xs text-gray-400">{{ t('mcp.noTools') }}</div>

                <n-collapse v-if="srv.last_error" :default-expanded-names="[]">
                  <n-collapse-item :title="t('mcp.lastError')" :name="srv.name">
                    <pre class="text-xs whitespace-pre-wrap break-all text-red-500 m-0">{{ srv.last_error }}</pre>
                  </n-collapse-item>
                </n-collapse>
              </div>
            </div>
          </template>
        </n-spin>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshOutline } from '@vicons/ionicons5'
import { getMcpServers, type McpServersData, type McpServerState } from '@/api/mcp'
import { extractApiError } from '@/utils/apiError'

const { t, te } = useI18n()

const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const data = ref<McpServersData | null>(null)

async function loadServers() {
  loading.value = true
  loadError.value = false
  try {
    const res = await getMcpServers()
    data.value = res.data.data
  } catch (error: unknown) {
    loadError.value = true
    loadErrorMsg.value = extractApiError(error, t('common.failed'))
  } finally {
    loading.value = false
  }
}

function stateText(state: McpServerState): string {
  const key = `mcp.state.${state}`
  return te(key) ? t(key) : state
}

function stateTagType(state: McpServerState): 'success' | 'error' | 'info' | 'warning' | 'default' {
  if (state === 'connected') return 'success'
  if (state === 'dead') return 'error'
  if (state === 'not_connected') return 'warning'
  if (state === 'connecting') return 'info'
  return 'default'
}

onMounted(() => {
  loadServers()
})
</script>
