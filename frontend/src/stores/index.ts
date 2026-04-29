import { createPinia } from 'pinia'
import type { App } from 'vue'

const pinia = createPinia()

export function installPinia(app: App) {
  app.use(pinia)
}

export * from './app'
export * from './user'
export * from './chat'