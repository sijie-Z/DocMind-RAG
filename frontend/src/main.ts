import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'virtual:uno.css'

import naive from 'naive-ui'

import App from './App.vue'
import router from './router'
import { setRouter } from './utils/request'
import i18n, { prepareCurrentLocale } from './locales'
import './styles/index.css'

// 将 router 实例注册到 axios 拦截器，以便 401 时正确跳转登录页
setRouter(router)

const pinia = createPinia()

async function bootstrap() {
  // 首屏瘦身（E5）：非默认语言包为动态 chunk——先就绪「当前语言」再挂载，
  // 保证首帧即为用户语言（默认 zh 已内置，此处零等待）。
  await prepareCurrentLocale()

  const app = createApp(App)
  app.use(pinia)
  app.use(router)
  app.use(i18n)
  app.use(naive)
  app.mount('#app')
}

void bootstrap()
