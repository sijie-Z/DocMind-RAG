/**
 * vue-virtual-scroller 的测试直通替身（E5 起共享）。
 *
 * 背景：E5 把虚拟滚动从 main.ts 全局注册改为 ChatMessages 局部引入，组件经
 * `<script setup>` 直接绑定解析，测试侧 `global.components` 无法再覆盖。
 * 各用例改用 `vi.mock('vue-virtual-scroller', ...)` 指向本文件的直通 stub，
 * 复现「按 items 逐项渲染 default slot」的行为（与改前全局 stub 语义一致）。
 */
import { defineComponent, h } from 'vue'

/** 按 items 逐项渲染 default slot（等价于改前各测试内的 DynamicScroller stub） */
export const DynamicScrollerStub = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () =>
      h('div', props.items.map((item, index) => slots.default?.({ item, index, active: true })))
  },
})

/** 直通容器：原样渲染 default slot */
export const DynamicScrollerItemStub = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})
