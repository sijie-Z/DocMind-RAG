import { describe, it, expect } from 'vitest'
import { PROVIDER_LABEL_KEYS, providerLabelKey } from '@/utils/providerLabels'
import zh from '@/locales/zh'

describe('providerLabels · 共享映射', () => {
  it('个人目录五个 id 都有四语键', () => {
    for (const id of ['deepseek', 'ollama', 'openai', 'zhipu', 'custom']) {
      expect(providerLabelKey(id)).toBe(PROVIDER_LABEL_KEYS[id])
      expect(providerLabelKey(id)).toMatch(/^profile\.provider/)
    }
  })

  it('组织页历史 id（qwen/step/local）也有键', () => {
    expect(providerLabelKey('qwen')).toBe('profile.providerQwen')
    expect(providerLabelKey('step')).toBe('profile.providerStepfun')
    expect(providerLabelKey('local')).toBe('profile.providerLocal')
  })

  it('未知 id 返回 undefined（调用方保留裸 id/后端 name 兜底）', () => {
    expect(providerLabelKey('azure')).toBeUndefined()
    expect(providerLabelKey('')).toBeUndefined()
  })

  it('映射键在中文词条中均可解析（不会渲染出 key 字符串）', () => {
    const profile = zh.profile as unknown as Record<string, string>
    for (const key of Object.values(PROVIDER_LABEL_KEYS)) {
      const short = key.replace('profile.', '')
      expect(profile[short], `${key} 缺少中文词条`).toBeTruthy()
    }
  })

  it('原型链键不命中（__proto__/constructor）', () => {
    expect(providerLabelKey('__proto__')).toBeUndefined()
    expect(providerLabelKey('constructor')).toBeUndefined()
  })
})
