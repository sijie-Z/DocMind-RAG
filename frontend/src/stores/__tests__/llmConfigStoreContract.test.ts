/**
 * LLM 配置 store 契约（T15 · 响应解包收敛）。
 *
 * 真响应形状（backend/app/api/v1/endpoints/llm_config.py）：
 *   列表  GET /llm-config          → {success: true, data: [<config>], total}
 *   供应商 GET /llm-config/providers → {success: true, data: [{key, label}]}
 *   创建/更新                      → {success: true, data: <config>, message}
 * 迁移 apiUnwrap 后形状必须不变：数组入 store、单对象回填列表。
 *
 * 变异点：把 list 分支退回读整信封（res.data）→ 「列表/供应商落 store」用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/llmConfig', () => ({
  llmConfigApi: {
    list: vi.fn(),
    providers: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    setDefault: vi.fn(),
  },
}))

import { llmConfigApi, type LLMProviderConfig } from '@/api/llmConfig'
import { useLLMConfigStore } from '../llmConfigStore'

// 真响应字段：_public_config 输出 id/provider/config_name/api_key/api_url/model/…
const CONFIG: LLMProviderConfig = {
  id: 'cfg-1',
  provider: 'deepseek',
  config_name: '默认',
  api_key: 'sk-placeholder',
  api_url: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  temperature: 0.7,
  is_default: true,
  created_at: '2026-10-10T04:44:58',
}

const PROVIDERS = [
  { key: 'deepseek', label: 'DeepSeek' },
  { key: 'openai', label: 'OpenAI' },
]

const LIST_RESPONSE = { data: { success: true, data: [CONFIG], total: 1 } }
const PROVIDERS_RESPONSE = { data: { success: true, data: PROVIDERS } }

describe('llmConfigStore · 响应形状契约（{success, data} 信封）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('列表与供应商：信封解一层后数组原样落 store', async () => {
    vi.mocked(llmConfigApi.list).mockResolvedValue(LIST_RESPONSE as never)
    vi.mocked(llmConfigApi.providers).mockResolvedValue(PROVIDERS_RESPONSE as never)

    const store = useLLMConfigStore()
    await store.fetchAll()

    expect(store.configs).toEqual([CONFIG])
    expect(store.providers).toEqual(PROVIDERS)
    expect(store.loaded).toBe(true)
  })

  it('创建：{success, data} 解一层得到单配置并追加进列表', async () => {
    vi.mocked(llmConfigApi.list).mockResolvedValue(LIST_RESPONSE as never)
    vi.mocked(llmConfigApi.providers).mockResolvedValue(PROVIDERS_RESPONSE as never)
    const created = { ...CONFIG, id: 'cfg-2', config_name: '备用', is_default: false }
    vi.mocked(llmConfigApi.create).mockResolvedValue({
      data: { success: true, data: created, message: "配置 '备用' 已创建" },
    } as never)

    const store = useLLMConfigStore()
    await store.fetchAll()
    const result = await store.create(created as never)

    expect(result).toEqual(created)
    expect(store.configs.map((c) => c.id)).toEqual(['cfg-1', 'cfg-2'])
  })

  it('更新：{success, data} 解一层得到单配置并原位替换', async () => {
    vi.mocked(llmConfigApi.list).mockResolvedValue(LIST_RESPONSE as never)
    vi.mocked(llmConfigApi.providers).mockResolvedValue(PROVIDERS_RESPONSE as never)
    const updated = { ...CONFIG, model: 'deepseek-reasoner' }
    vi.mocked(llmConfigApi.update).mockResolvedValue({
      data: { success: true, data: updated, message: "配置 '默认' 已更新" },
    } as never)

    const store = useLLMConfigStore()
    await store.fetchAll()
    const result = await store.update('cfg-1', updated as never)

    expect(result).toEqual(updated)
    expect(store.configs[0].model).toBe('deepseek-reasoner')
  })
})
