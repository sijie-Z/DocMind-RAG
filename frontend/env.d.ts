/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  readonly VITE_WS_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// 重点：一定要有这一段，才能消灭 main.ts 和 search.vue 里的红线
declare module 'vue-virtual-scroller' {
  import { Plugin } from 'vue'
  const plugin: Plugin
  export default plugin
  export const DynamicScroller: any
  export const DynamicScrollerItem: any
  export const RecycleScroller: any
}
