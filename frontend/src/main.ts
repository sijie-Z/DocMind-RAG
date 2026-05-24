import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'virtual:uno.css'

import VueVirtualScroller from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'

import App from './App.vue'
import router from './router'
import { setRouter } from './utils/request'
import i18n from './locales'
import './styles/index.css'

// 将 router 实例注册到 axios 拦截器，以便 401 时正确跳转登录页
setRouter(router)

const pinia = createPinia()

const app = createApp(App)
app.use(pinia)
app.use(router)
app.use(i18n)
app.use(VueVirtualScroller)
app.mount('#app')
