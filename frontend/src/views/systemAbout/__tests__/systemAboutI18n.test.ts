/**
 * 关于页 i18n 判别用例。
 *
 * 修前：整页硬编码中文（tagline/系统信息/7 组描述/6 个亮点/12 个流程节点/
 * 页脚），且未接入 useI18n；en/ja/fr 下完全中文。
 * 修复：接入 useI18n，about.* 四语词条；highlights/dataFlow 改 computed
 * （依赖 t），切语言实时更新。
 *
 * 变异点：
 *  - highlights/dataFlow 退回「写死中文」的 setup 期常量 → en 断言红；
 *  - highlights/dataFlow 退回「保留 t() 调用」的 setup 期常量 → 切换用例红
 *    （直接挂载式用例区分不出响应式与一次性求值，必须走切换路径）；
 *  - tagline 写死英文 → zh 用例的反向断言红（values.product 同串会遮蔽
 *    toContain，反向断言负责抓「硬编码英文」）；
 *  - 删除未被页面断言的词条 → keySnapshot/completeness 闸红（闸网兜底）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import { nextTick } from 'vue'

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
  const w = mount(SystemAbout, { global: { plugins: [i18n, router] } })
  return { w, i18n }
}

describe('关于页 · 四语化', () => {
  it('zh：tagline/系统信息/亮点/数据流/页脚全部渲染，且无英文串译残留', async () => {
    const { w } = await mountPage('zh')
    const text = w.text()
    expect(text).toContain('文档问答与合同核验平台')
    // 反向断言：tagline 若被写死英文，values.product 的同串会遮蔽上面的
    // toContain——这条负责抓「硬编码英文」类回归（评审 B 变异实证）
    expect(text, 'tagline 被写死英文').not.toContain('Document Q&A and Contract Review platform')
    expect(text).toContain('系统信息')
    expect(text).toContain('架构亮点')
    expect(text).toContain('混合检索')
    expect(text).toContain('数据流')
    expect(text).toContain('解析分块')
    expect(text).toContain('基于 FastAPI + Vue 3 + Elasticsearch + DeepSeek LLM 构建')
    w.unmount()
  })

  it('en：同页挂载即为英文（词条四语齐备）', async () => {
    const { w } = await mountPage('en')
    const text = w.text()
    expect(text).toContain('Document Q&A and Contract Review platform')
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

  it('已挂载页面切语言实时更新（computed 必须被 locale ref 追踪）', async () => {
    // 直接挂载式用例区分不出「响应式 computed」与「setup 期一次性求值」：
    // 两者在目标语挂载下产出相同文本。必须走 zh 挂载 → 翻 locale → 断言 en。
    const { w, i18n } = await mountPage('zh')
    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()
    await nextTick()

    const text = w.text()
    expect(text, '亮点未实时更新（可能已退化为 setup 常量）').toContain('Hybrid retrieval')
    expect(text, '数据流未实时更新').toContain('Parse & chunk')
    expect(text, '标题未实时更新').toContain('Architecture highlights')
    expect(text, '切换后亮点残留中文').not.toContain('混合检索')
    expect(text).not.toContain('解析分块')
    w.unmount()
  })
})

