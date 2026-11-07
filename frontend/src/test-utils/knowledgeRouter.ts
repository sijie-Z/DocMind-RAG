import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

const RouteStub = defineComponent({ name: 'RouteStub', render: () => null })

/**
 * 知识库相关组件的最小路由：useRouter()/RouterLink 在挂载时可用。
 * 只供测试使用，不参与生产打包。
 */
export function createKnowledgeTestRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: RouteStub },
      { path: '/knowledge', name: 'Knowledge', component: RouteStub },
      { path: '/knowledge/:documentId', name: 'KnowledgeDocument', component: RouteStub },
      { path: '/chat', name: 'Chat', component: RouteStub }
    ]
  })
}
