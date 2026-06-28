import { defineStore } from 'pinia'
import { ref } from 'vue'
import { llmConfigApi, type LLMProviderConfig, type ProviderOption } from '@/api/llmConfig'

export type { LLMProviderConfig, ProviderOption }

export const useLLMConfigStore = defineStore('llmConfig', () => {
  const configs = ref<LLMProviderConfig[]>([])
  const providers = ref<ProviderOption[]>([])
  const loading = ref(false)
  const loaded = ref(false)

  /** Load all configs and providers from backend */
  async function fetchAll() {
    if (loaded.value) return
    loading.value = true
    try {
      const [configsRes, providersRes] = await Promise.all([
        llmConfigApi.list(),
        llmConfigApi.providers(),
      ])
      configs.value = configsRes.data?.data || []
      providers.value = providersRes.data?.data || []
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  /** Force reload */
  async function reload() {
    loaded.value = false
    await fetchAll()
  }

  /** Get all configs for a provider */
  function getConfigsForProvider(provider: string): LLMProviderConfig[] {
    return configs.value.filter(c => c.provider === provider)
  }

  /** Get the default config for a provider */
  function getDefaultForProvider(provider: string): LLMProviderConfig | undefined {
    const mine = getConfigsForProvider(provider)
    return mine.find(c => c.is_default) || mine[0]
  }

  /** Get dropdown options for a provider (for n-select in node config) */
  function getOptionsForProvider(provider: string): { label: string; value: string }[] {
    return getConfigsForProvider(provider).map(c => ({
      label: `${c.config_name || c.model}${c.is_default ? ' (默认)' : ''}`,
      value: c.id,
    }))
  }

  /** Map provider key to label */
  function getProviderLabel(key: string): string {
    return providers.value.find(p => p.key === key)?.label || key
  }

  /** Create a new config */
  async function create(data: Omit<LLMProviderConfig, 'id'>): Promise<LLMProviderConfig> {
    const res = await llmConfigApi.create(data)
    const created = res.data?.data
    if (created) {
      configs.value.push(created)
    }
    return created
  }

  /** Full update */
  async function update(configId: string, data: Omit<LLMProviderConfig, 'id'>) {
    const res = await llmConfigApi.update(configId, data)
    const updated = res.data?.data
    if (updated) {
      const idx = configs.value.findIndex(c => c.id === configId)
      if (idx >= 0) configs.value[idx] = updated
    }
    return updated
  }

  /** Delete a config */
  async function remove(configId: string) {
    await llmConfigApi.delete(configId)
    configs.value = configs.value.filter(c => c.id !== configId)
  }

  /** Set a config as default */
  async function setDefault(configId: string) {
    await llmConfigApi.setDefault(configId)
    // Reload to get accurate is_default states
    await reload()
  }

  return {
    configs,
    providers,
    loading,
    loaded,
    fetchAll,
    reload,
    getConfigsForProvider,
    getDefaultForProvider,
    getOptionsForProvider,
    getProviderLabel,
    create,
    update,
    remove,
    setDefault,
  }
})
