/**
 * Provider id → i18n 标签键的唯一映射。
 *
 * 个人设置与组织设置共用：后端目录返回的 name 仍作为未知 id 的兜底
 * （个人目录只覆盖前五个 id；组织页的历史选项包含 qwen/step/local）。
 */
export const PROVIDER_LABEL_KEYS: Record<string, string> = {
  deepseek: 'profile.providerDeepseek',
  ollama: 'profile.providerOllama',
  openai: 'profile.providerOpenai',
  zhipu: 'profile.providerZhipu',
  custom: 'profile.providerCustom',
  // 组织设置的组织级默认 provider（与个人目录不是同一套 id）
  qwen: 'profile.providerQwen',
  step: 'profile.providerStepfun',
  local: 'profile.providerLocal',
}

export function providerLabelKey(id: string): string | undefined {
  // own-property：'__proto__' 之类的恶意存储值不得命中原型链。
  return Object.hasOwn(PROVIDER_LABEL_KEYS, id) ? PROVIDER_LABEL_KEYS[id] : undefined
}
