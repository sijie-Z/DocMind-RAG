import request from '@/utils/request'

export interface LLMProviderConfig {
  id: string
  provider: string
  config_name: string
  api_key: string
  api_url: string
  model: string
  temperature: number
  is_default: boolean
  created_at?: string
}

export interface ProviderOption {
  key: string
  label: string
}

export const llmConfigApi = {
  /** List all configs */
  list: () => request.get('/llm-config'),

  /** List supported providers */
  providers: () => request.get('/llm-config/providers'),

  /** Get default config for a provider */
  getDefault: (provider: string) => request.get(`/llm-config/default/${provider}`),

  /** Get single config detail */
  get: (configId: string) => request.get(`/llm-config/detail/${configId}`),

  /** Create a new config */
  create: (data: Omit<LLMProviderConfig, 'id'>) => request.post('/llm-config', data),

  /** Full update of an existing config */
  update: (configId: string, data: Omit<LLMProviderConfig, 'id'>) =>
    request.put(`/llm-config/${configId}`, data),

  /** Partial update of a config */
  patch: (configId: string, data: Partial<LLMProviderConfig>) =>
    request.patch(`/llm-config/${configId}`, data),

  /** Delete a config */
  delete: (configId: string) => request.delete(`/llm-config/${configId}`),

  /** Set a config as default */
  setDefault: (configId: string) => request.post(`/llm-config/${configId}/default`),
}
