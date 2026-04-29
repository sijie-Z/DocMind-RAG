import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import 'virtual:uno.css'

// ⚠️ 重点：千万不要加具体的 .umd.js 路径，直接写包名
import VueVirtualScroller from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'

import App from './App.vue'
import { routes } from './router'
import { setupInterceptors } from './utils/request'
import i18n from './locales'
import './styles/index.css'

const router = createRouter({
  history: createWebHistory((import.meta as any).env.BASE_URL),
  routes,
})

const pinia = createPinia()
setupInterceptors(router)

const app = createApp(App)
app.use(pinia)
app.use(router)
app.use(i18n)
app.use(VueVirtualScroller) // 全局注册组件
app.mount('#app')
