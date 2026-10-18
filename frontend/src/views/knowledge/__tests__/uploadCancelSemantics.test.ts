/**
 * E9 上传取消语义判别用例（B7）。
 *
 * 期望行为：
 * - 任务进行中（uploading / processing）点「×」= 真取消：通过 AbortController
 *   中止 uploadKnowledgeBase 请求（第三参 signal），任务从列表移除，不误报
 *   「上传失败」，也不刷新列表（文档不出现）。
 * - 任务已终态（completed / error）点「×」= 只从列表移除。
 * - 取消一个上传不得影响并发中的其他上传；全局 uploading 仍反映剩余在途任务。
 * - 面板按钮对进行中/终态任务给出与实际行为一致的提示（取消上传 / 从列表移除）。
 *
 * 变异点（任一发生 → 红）：
 * - 视图不把 signal 传给 uploadKnowledgeBase（回退为「×」只删列表）；
 * - removeUploadTask 不 abort / abort 错对象；
 * - 取消后仍走 catch 误报「上传失败」；
 * - 取消 A 连带影响 B 的完成或 uploading 状态；
 * - 面板按钮不区分进行中/终态语义（title/aria-label）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import {
  NMessageProvider, NDialogProvider, NCheckbox, NTooltip,
  NButton, NDrawer, NDrawerContent, NFormItem, NInput, NProgress, NBadge,
  NModal, NSpin, NUpload, NUploadDragger, NSelect, NText, NP, NIcon,
} from 'naive-ui'

import KnowledgeView from '../index.vue'
import UploadPanel from '../components/KnowledgeUploadPanel.vue'
import { getKnowledgeBases, uploadKnowledgeBase } from '@/api/knowledge'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getKnowledgeBases: vi.fn(),
    getDocumentDetail: vi.fn(),
    getDocumentContent: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    batchDeleteKnowledgeBases: vi.fn(),
    rebuildKnowledgeBase: vi.fn(),
    uploadKnowledgeBase: vi.fn(),
  }
})
vi.mock('@/api/rag', () => ({
  getRagMetrics: vi.fn().mockResolvedValue({ data: { data: null } }),
}))

const mockedGetKnowledgeBases = vi.mocked(getKnowledgeBases)
const mockedUpload = vi.mocked(uploadKnowledgeBase)

const listResponse = (docs: unknown[]) => ({
  data: {
    success: true,
    message: '获取成功',
    data: { data: docs, total: docs.length, page: 1, page_size: 12 },
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

/** 上传面板桩：暴露「选文件 / 开始上传」按钮与每个任务行（含 ×），并透出 uploading 状态 */
const PanelStub = defineComponent({
  name: 'KnowledgeUploadPanel',
  props: {
    showUploadModal: { type: Boolean, default: false },
    showTaskList: { type: Boolean, default: false },
    uploading: { type: Boolean, default: false },
    fileList: { type: Array, default: () => [] },
    uploadForm: { type: Object, default: () => ({ title: '', tags: [], description: '' }) },
    tagOptions: { type: Array, default: () => [] },
    canUpload: { type: Boolean, default: false },
    activeTasks: { type: Array, default: () => [] },
  },
  emits: [
    'update:showUploadModal', 'update:showTaskList', 'update:uploadForm',
    'update:fileList', 'upload', 'removeTask', 'clearFinished',
  ],
  setup(props, { emit }) {
    const pickFile = (name: string) => {
      emit('update:uploadForm', {
        ...(props.uploadForm as Record<string, unknown>),
        file: new File(['x'], name),
      })
      emit('update:fileList', [{ id: name, name }])
    }
    return () =>
      h('div', { class: 'panel-stub', 'data-uploading': String(props.uploading) }, [
        h('button', { class: 'pick-a', onClick: () => pickFile('a.pdf') }, 'pick-a'),
        h('button', { class: 'pick-b', onClick: () => pickFile('b.pdf') }, 'pick-b'),
        h('button', { class: 'start-upload', onClick: () => emit('upload') }, 'start'),
        ...(props.activeTasks as Array<{ id: string; name: string; status: string }>).map((task) =>
          h(
            'div',
            { class: 'task-row', 'data-name': task.name, 'data-status': task.status },
            [
              h(
                'button',
                { class: 'cancel-task', 'data-cancel': task.name, onClick: () => emit('removeTask', task.id) },
                'x',
              ),
            ],
          ),
        ),
      ])
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo

  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, {
        default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }),
      }),
  })

  return mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NCheckbox, NTooltip },
      stubs: {
        KnowledgeUploadPanel: PanelStub,
        KnowledgeDetailModals: true,
        Pagination: true,
        Select: true,
        Input: true,
        RadioGroup: true,
        RadioButton: true,
        Grid: SlotStub('Grid'),
        Gi: SlotStub('Gi'),
        Spin: SlotStub('Spin'),
        Tag: SlotStub('Tag'),
      },
    },
  })
}

/** 选文件 + 点开始上传（面板桩 → 视图 uploadFile） */
async function startUpload(wrapper: ReturnType<typeof mountView>, pick: 'pick-a' | 'pick-b') {
  await wrapper.find(`.${pick}`).trigger('click')
  await nextTick()
  await wrapper.find('.start-upload').trigger('click')
  await nextTick()
}

/** 模拟 axios 对 abort 的拒绝形状（CanceledError / ERR_CANCELED） */
function rejectOnAbort(signal?: AbortSignal) {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener('abort', () => {
      const err = Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' })
      reject(err)
    })
  })
}

beforeEach(() => {
  mockedGetKnowledgeBases.mockReset()
  mockedUpload.mockReset()
  mockedGetKnowledgeBases.mockResolvedValue(listResponse([]) as never)
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('KnowledgeView · 上传取消语义（E9/B7）', () => {
  it('上传中点击 × → AbortController 中止请求、任务移除、不误报上传失败、不刷新列表', async () => {
    const signals: Array<AbortSignal | undefined> = []
    mockedUpload.mockImplementation((_data, _onProgress, signal) => {
      signals.push(signal)
      return rejectOnAbort(signal) as never
    })

    const wrapper = mountView()
    await flushPromises()

    await startUpload(wrapper, 'pick-a')
    expect(signals).toHaveLength(1)
    const signal = signals[0]
    expect(signal, '视图必须把 AbortSignal 传给 uploadKnowledgeBase').toBeTruthy()
    expect(signal!.aborted).toBe(false)
    expect(wrapper.find('[data-name="a.pdf"]').attributes('data-status')).toBe('uploading')

    await wrapper.find('[data-cancel="a.pdf"]').trigger('click')
    await flushPromises()
    await nextTick()

    expect(signal!.aborted, '× 必须真正中止请求（AbortController.abort）').toBe(true)
    expect(wrapper.find('[data-name="a.pdf"]').exists(), '取消后任务应从列表移除').toBe(false)
    // 取消不刷新列表：只有挂载时那一次加载
    expect(mockedGetKnowledgeBases).toHaveBeenCalledTimes(1)
    // 用户主动取消不是失败：不得报「上传失败」
    expect(document.body.textContent || '').not.toContain('上传失败')
  })

  it('取消一个上传不影响并发中的另一个上传（防回退）', async () => {
    let resolveB!: (value: unknown) => void
    const signals: Array<AbortSignal | undefined> = []
    mockedUpload
      .mockImplementationOnce((_data, _onProgress, signal) => {
        signals.push(signal)
        return rejectOnAbort(signal) as never
      })
      .mockImplementationOnce((_data, _onProgress, signal) => {
        signals.push(signal)
        return new Promise((resolve) => { resolveB = resolve }) as never
      })

    const wrapper = mountView()
    await flushPromises()

    await startUpload(wrapper, 'pick-a')
    await wrapper.find('[data-cancel="a.pdf"]').trigger('click')
    await flushPromises()
    await nextTick()
    expect(signals[0]?.aborted).toBe(true)
    expect(wrapper.find('.panel-stub').attributes('data-uploading')).toBe('false')

    await startUpload(wrapper, 'pick-b')
    // B 在途：A 的取消不得把全局 uploading 清成 false
    expect(wrapper.find('.panel-stub').attributes('data-uploading')).toBe('true')

    resolveB({ data: { success: true, data: { id: 'doc-b' } } })
    await flushPromises()
    await nextTick()

    const done = wrapper.find('[data-name="b.pdf"]')
    expect(done.exists(), 'B 应正常完成并留在列表').toBe(true)
    expect(done.attributes('data-status')).toBe('completed')
    expect(wrapper.find('.panel-stub').attributes('data-uploading')).toBe('false')
    // 初始加载 1 次 + B 成功后刷新 1 次；取消 A 不触发刷新
    expect(mockedGetKnowledgeBases).toHaveBeenCalledTimes(2)
  })
})

describe('KnowledgeUploadPanel · × 文案与实际行为一致（E9/B7）', () => {
  type TaskStatus = 'uploading' | 'processing' | 'completed' | 'error'
  function mountPanel(tasks: Array<{ id: string; name: string; progress: number; status: TaskStatus }>) {
    return mount(UploadPanel, {
      props: {
        showUploadModal: false,
        showTaskList: true,
        uploading: false,
        fileList: [],
        uploadForm: { title: '', tags: [], description: '' },
        tagOptions: [],
        canUpload: false,
        activeTasks: tasks,
      },
      global: {
        plugins: [i18n],
        // 生产由 app.use(naive) 全局注册；测试里注册真组件以渲染抽屉/按钮
        components: {
          NButton, NDrawer, NDrawerContent, NFormItem, NInput, NProgress, NBadge,
          NModal, NSpin, NUpload, NUploadDragger, NSelect, NText, NP, NIcon,
        },
      },
      attachTo: document.body,
    })
  }

  it('进行中任务提示「取消上传」，终态任务提示「从列表移除」；点击仍发出 removeTask', async () => {
    const wrapper = mountPanel([
      { id: 't1', name: 'uploading.pdf', progress: 42, status: 'uploading' },
      { id: 't2', name: 'done.pdf', progress: 100, status: 'completed' },
    ])
    await flushPromises()
    await nextTick()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const cancelBtn = buttons.find((b) => b.getAttribute('title') === '取消上传')
    const removeBtn = buttons.find((b) => b.getAttribute('title') === '从列表移除')
    expect(cancelBtn, '进行中任务 × 应提示「取消上传」').toBeTruthy()
    expect(removeBtn, '终态任务 × 应提示「从列表移除」').toBeTruthy()

    cancelBtn!.click()
    await nextTick()
    expect(wrapper.emitted('removeTask')).toEqual([['t1']])

    wrapper.unmount()
  })
})
