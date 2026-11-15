/**
 * 关于页 i18n 判别用例。
 *
 * 修前：整页硬编码中文（tagline/系统信息/7 组描述/6 个亮点/12 个流程节点/
 * 页脚），且未接入 useI18n；en/ja/fr 下完全中文。
 * 修复：接入 useI18n，about.* 四语词条；highlights/dataFlow 改 computed
 * （依赖 t），切语言实时更新。
 *
 * 变异点：
 *  - highlights/dataFlow 退回 setup 期常量（或写死中文）→ en 断言红；
 *  - 删除任一 about.* 词条 → vue-tsc/断言红。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'

import SystemAbout from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

vi.mock('@/composables/useAppVersion', () => ({ useAppVersion: () => ({ value: '1.0.0' }) }))

/** 每个用例独立 i18n + router：面包屑依赖 route.name/meta，Dashboard 需在册 */
function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
      {
        path: '/about',
        name: 'SystemAbout',
        component: { template: '<div />' },
        meta: { title: 'menu.systemAbout', activeMenu: 'SystemAbout' },
      },
    ],
  })
  void router.push('/about')
  return router
}

async function mountPage(locale: 'zh' | 'en' = 'zh') {
  const router = makeRouter()
  await router.isReady()
  const i18n = createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'en',
    messages: { zh, en },
  })
  return mount(SystemAbout, { global: { plugins: [i18n, router] } })
}

describe('关于页 · 四语化', () => {
  it('zh：tagline/系统信息/亮点/数据流/页脚全部渲染', async () => {
    const w = await mountPage('zh')
    const text = w.text()
    expect(text).toContain('文档问答与合同核验平台')
    expect(text).toContain('系统信息')
    expect(text).toContain('架构亮点')
    expect(text).toContain('混合检索')
    expect(text).toContain('数据流')
    expect(text).toContain('解析分块')
    expect(text).toContain('基于 FastAPI + Vue 3 + Elasticsearch + DeepSeek LLM 构建')
    w.unmount()
  })

  it('en：同页挂载即为英文（词条四语齐备）', async () => {
    const w = await mountPage('en')
    const text = w.text()
    expect(text).toContain('Document Q&A and contract verification platform')
    expect(text).toContain('System information')
    expect(text).toContain('Architecture highlights')
    expect(text, '亮点标题未跟随语言').toContain('Hybrid retrieval')
    expect(text).toContain('Data flow')
    expect(text, '数据流节点未跟随语言').toContain('Parse & chunk')
    expect(text).toContain('Built on FastAPI + Vue 3 + Elasticsearch + DeepSeek LLM')
    expect(text, '切换后仍残留中文标题').not.toContain('系统信息')
    expect(text).not.toContain('架构亮点')
    w.unmount()
  })
})
