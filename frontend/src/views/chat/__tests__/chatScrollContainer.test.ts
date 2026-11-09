/**
 * ChatMessages 滚动容器必须暴露给父级（回到底部/自动跟随依赖它）。
 *
 * 修前：模板写了 ref="chatContainer" 但 script setup 里没有对应绑定，父级
 * useChatMessages 的 chatContainer 永远为 null，handleScroll/scrollToBottom
 * 全部空转。修复：声明 ref + defineExpose，父级回填。
 *
 * 变异点：删除 ref 声明或 defineExpose → scrollEl 为 undefined，本用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'

import ChatMessages from '../components/ChatMessages.vue'
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

describe('ChatMessages · 滚动容器暴露', () => {
  it('scrollEl 指向根滚动容器', () => {
    const wrapper = mount(ChatMessages, {
      props: {
        messages: [],
        isLoading: false,
        showBackToBottom: false,
        suggestions: [],
      },
      global: { plugins: [i18n] },
    })

    const exposed = wrapper.vm as unknown as { scrollEl?: HTMLElement }
    expect(exposed.scrollEl).toBeTruthy()
    expect(exposed.scrollEl).toBe(wrapper.element)
    expect((wrapper.element as HTMLElement).className).toContain('overflow-y-auto')
    wrapper.unmount()
  })
})
