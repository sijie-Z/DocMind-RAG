/**
 * 上传组件 accept 放行图片判别用例（图片 OCR 支线配套，协调者追加）。
 *
 * 变更：accept 在原有文档格式基础上补位图扩展名
 *   .png,.jpg,.jpeg,.webp,.bmp（后端解析支持由 OCR 支线提供，前端只管放行）；
 *   dragHint 四语同步提到"图片"。
 *
 * 变异点：从 accept 里去掉任一图片扩展名（或还原提示文案）→ 对应用例精确红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { NUpload } from 'naive-ui'

import UploadPanel from '../components/KnowledgeUploadPanel.vue'
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountPanel() {
  return mount(UploadPanel, {
    props: {
      showUploadModal: true,
      showTaskList: false,
      uploading: false,
      fileList: [],
      uploadForm: { title: '', tags: [], description: '' },
      tagOptions: [],
      canUpload: false,
      activeTasks: [],
    },
    global: {
      plugins: [i18n],
      // n-upload 生产由 app.use(naive) 全局注册；注册真组件以渲染 accept 输入框
      components: { NUpload },
    },
  })
}

describe('KnowledgeUploadPanel · accept 放行图片（OCR 支线配套）', () => {
  it('原有文档格式保持 + 补位图扩展名（png/jpg/jpeg/webp/bmp）', () => {
    const wrapper = mountPanel()
    const input = wrapper.find('input[type="file"]')
    expect(input.exists()).toBe(true)

    const accept = input.attributes('accept') || ''
    // 原有 7 项不动
    for (const ext of ['.pdf', '.docx', '.xlsx', '.xls', '.txt', '.md', '.csv']) {
      expect(accept).toContain(ext)
    }
    // 新增位图 5 项
    for (const ext of ['.png', '.jpg', '.jpeg', '.webp', '.bmp']) {
      expect(accept).toContain(ext)
    }
  })

  it('拖拽提示文案同步提到"图片"', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('图片')
  })
})
