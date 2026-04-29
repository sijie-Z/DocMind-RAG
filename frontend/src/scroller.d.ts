// frontend/src/scroller.d.ts
declare module 'vue-virtual-scroller' {
  import { Plugin } from 'vue'
  const plugin: Plugin
  export default plugin
  export const DynamicScroller: any
  export const DynamicScrollerItem: any
  export const RecycleScroller: any
}
