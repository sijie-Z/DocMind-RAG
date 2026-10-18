import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import Layout from '@/layouts/index.vue'
import { useUserStore } from '@/stores/user'
import { getToken, isTokenExpired, removeToken } from '@/utils/auth'
import { silentRefreshAccessToken } from '@/utils/request'

/** 路由 meta 扩展（T9 定位三件套）：生效侧栏高亮键 */
declare module 'vue-router' {
  interface RouteMeta {
    /**
     * 生效高亮键（naive-ui 菜单 key = menu.ts 的 routeName 空间）：
     * 隐藏页显式声明其菜单面归属（如 KnowledgeGraph → 'Knowledge'）；
     * 缺省回退 route.name（菜单页自身即键）。契约由 namingConsistency 定位层守护。
     */
    activeMenu?: string
  }
}

/** 路由 meta 扩展（T9 定位三件套）：生效侧栏高亮键 */
declare module 'vue-router' {
  interface RouteMeta {
    /**
     * 生效高亮键（naive-ui 菜单 key = menu.ts 的 routeName 空间）：
     * 隐藏页显式声明其菜单面归属（如 KnowledgeGraph → 'Knowledge'）；
     * 缺省回退 route.name（菜单页自身即键）。契约由 namingConsistency 定位层守护。
     */
    activeMenu?: string
  }
}

let sessionValidated = false
let lastValidatedAt = 0
// admin 路由重新校验角色的时间间隔（毫秒）：超过则重新调用 getUserInfo 刷新角色
const ADMIN_REVALIDATE_INTERVAL = 5 * 60 * 1000

export const routes: RouteRecordRaw[] = [
  {
    path: '/firsthome', // 添加 firsthome 路由
    name: 'FirstHome',
    component: () => import('@/views/firsthome/index.vue'),
    meta: {
      title: 'router.welcome',
      requiresAuth: false
    }
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: {
      title: 'router.login',
      requiresAuth: false
    }
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/login/index.vue'),
    meta: {
      title: 'router.register',
      requiresAuth: false
    }
  },
  // 法务占位页：登录页 /privacy、/terms 链接的真实落点（匿名可访问）。
  // 正式法务文本上线后，只需替换 views/legal/index.vue 的内容，路由保持稳定。
  {
    path: '/privacy',
    name: 'Privacy',
    component: () => import('@/views/legal/index.vue'),
    meta: {
      title: 'legal.privacy.title',
      requiresAuth: false
    }
  },
  {
    path: '/terms',
    name: 'Terms',
    component: () => import('@/views/legal/index.vue'),
    meta: {
      title: 'legal.terms.title',
      requiresAuth: false
    }
  },
  {
    path: '/',
    component: Layout,
    redirect: '/firsthome',
    meta: {
      requiresAuth: true
    },
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: {
          // T5 命名归一：本页四处（菜单/顶栏 meta.title/h1）统一引用 menu.home，
          // 与侧边栏一致（值以菜单语义为准）；menu.dashboard 死键已随 T9 删除。
          title: 'menu.home',
          icon: 'home'
        }
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/chat/index.vue'),
        meta: {
          title: 'menu.chat',
          icon: 'chat'
        }
      },
      {
        path: 'conversations',
        name: 'Conversations',
        component: () => import('@/views/conversations/index.vue'),
        meta: {
          title: 'menu.conversations',
          icon: 'history'
        }
      },
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('@/views/knowledge/index.vue'),
        meta: {
          title: 'menu.knowledge',
          icon: 'book'
        }
      },
      {
        path: 'knowledge/graph',
        name: 'KnowledgeGraph',
        component: () => import('@/views/knowledge/graph.vue'),
        meta: {
          title: 'router.knowledgeGraph',
          // T9：图谱 = 知识库子页，侧栏高亮「知识库」（修 P1-18 永不高亮）
          activeMenu: 'Knowledge',
          icon: 'git-network'
        }
      },
      {
        path: 'prompts',
        name: 'Prompts',
        component: () => import('@/views/prompts/index.vue'),
        meta: {
          title: 'menu.prompts',
          icon: 'terminal'
        }
      },
      {
        path: 'search',
        name: 'Search',
        component: () => import('@/views/search.vue'),
        meta: {
          title: 'menu.search',
          icon: 'search'
        }
      },
      {
        path: 'organizations',
        name: 'Organizations',
        component: () => import('@/views/organizations/index.vue'),
        meta: {
          title: 'menu.organizations',
          icon: 'organization',
          requiresAdmin: true
        }
      },
      {
        path: 'users',
        name: 'Users',
        component: () => import('@/views/users/index.vue'),
        meta: {
          title: 'menu.users',
          icon: 'users',
          requiresAdmin: true
        }
      },
      {
        path: 'audit-logs',
        name: 'AuditLogs',
        component: () => import('@/views/admin/audit/index.vue'),
        meta: {
          title: 'menu.auditLogs',
          icon: 'document-text',
          requiresAdmin: true
        }
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/profile/index.vue'),
        meta: {
          title: 'menu.personalSettings',
          icon: 'user'
        }
      },
      {
        path: 'workflow',
        name: 'Workflow',
        component: () => import('@/views/workflow/index.vue'),
        meta: {
          title: 'menu.workflow',
          icon: 'git-network'
        }
      },
      {
        path: 'workflow/editor',
        name: 'WorkflowEditor',
        component: () => import('@/views/workflow/editor.vue'),
        meta: {
          title: 'router.workflowEditor',
          // T9：编辑器 = Agent 工作流子页，侧栏高亮「Agent 工作流」
          activeMenu: 'Workflow',
          icon: 'git-network'
        }
      },
      {
        path: 'workflow/memory',
        name: 'AgentMemory',
        component: () => import('@/views/workflow/memory.vue'),
        meta: {
          title: 'router.agentMemory',
          // T9：记忆 = Agent 工作流子页（入口在工作流页），侧栏高亮「Agent 工作流」
          activeMenu: 'Workflow',
          icon: 'database'
        }
      },
      {
        path: 'agent',
        name: 'Agent',
        component: () => import('@/views/agent/index.vue'),
        meta: {
          title: 'menu.agent',
          icon: 'hardware-chip'
        }
      },
      {
        path: 'scheduled-tasks',
        name: 'ScheduledTasks',
        component: () => import('@/views/scheduled-tasks/index.vue'),
        meta: {
          title: 'menu.scheduledTasks',
          icon: 'calendar'
        }
      },
      {
        path: 'system-help',
        name: 'SystemHelp',
        component: () => import('@/views/systemHelp/index.vue'),
        meta: {
          title: 'menu.systemHelp',
          // T9：菜单面 = 头像下拉项（无侧栏项），高亮键即用户下拉 key 空间
          activeMenu: 'SystemHelp',
          icon: 'help'
        }
      },
      {
        path: 'system-about',
        name: 'SystemAbout',
        component: () => import('@/views/systemAbout/index.vue'),
        meta: {
          title: 'menu.systemAbout',
          // T9：菜单面 = 头像下拉项（无侧栏项）
          activeMenu: 'SystemAbout',
          icon: 'information'
        }
      },
      {
        path: 'notifications',
        name: 'Notifications',
        component: () => import('@/views/notifications/index.vue'),
        meta: {
          title: 'menu.notifications',
          // T9：菜单面 = 铃铛弹层 + 头像下拉项（本轮新增下拉镜像项）
          activeMenu: 'Notifications',
          icon: 'notifications'
        }
      },
      {
        path: 'monitoring',
        name: 'Monitoring',
        component: () => import('@/views/monitoring/index.vue'),
        meta: {
          title: 'menu.monitoring',
          icon: 'activity',
          requiresAdmin: true
        }
      },
      {
        path: 'mcp-servers',
        name: 'McpServers',
        component: () => import('@/views/mcp-servers/index.vue'),
        meta: {
          title: 'menu.mcpServers',
          icon: 'server',
          requiresAdmin: true
        }
      }
    ]
  },
  // 历史写法兼容（常见 camelCase / 短别名误写）：放 catch-all 之前，
  // 重定向进真路由而非 404；保留查询串与哈希
  {
    path: '/systemHelp',
    redirect: (to) => ({ path: '/system-help', query: to.query, hash: to.hash })
  },
  {
    path: '/systemAbout',
    redirect: (to) => ({ path: '/system-about', query: to.query, hash: to.hash })
  },
  {
    path: '/help',
    redirect: (to) => ({ path: '/system-help', query: to.query, hash: to.hash })
  },
  {
    path: '/about',
    redirect: (to) => ({ path: '/system-about', query: to.query, hash: to.hash })
  },
  {
    // 真实 404：未知路径停留在原地址并渲染 404 页（不再静默弹回营销页）
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: {
      title: 'router.notFound',
      requiresAuth: false
    }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to, from, next) => {
  const userStore = useUserStore()
  let token = getToken()
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth)
  const requiresAdmin = to.matched.some((record) => record.meta.requiresAdmin)

  // token 本地判定过期 → 先尝试用 refresh_token 静默续期（与 401 拦截器共享单飞队列），
  // 续期成功继续原导航（刷新页面不再丢登录态）；续期失败才清号。
  // 注意：续期过程中不得删除 refresh_token。
  if (token && isTokenExpired()) {
    try {
      const newToken = await silentRefreshAccessToken()
      token = newToken
      userStore.token = newToken
      sessionValidated = false
      lastValidatedAt = 0
    } catch {
      removeToken()
      // 导航由下方 next() 统一完成（带 redirect）；不经过 logout 的异步跳转，避免覆盖 redirect
      userStore.logout({ navigate: false })
      token = undefined
      sessionValidated = false
      lastValidatedAt = 0
    }
  }

  // 已登录用户访问登录/注册页时，重定向到首页
  if (token && (to.name === 'Login' || to.name === 'Register')) {
    next({ name: 'Dashboard' })
    return
  }

  if (requiresAuth) {
    if (!token) {
      next({ name: 'Login', query: { redirect: to.fullPath } })
    } else {
      // 首次进入需鉴权页面时，验证 token 是否仍然有效
      // admin 路由：若上次校验已超过 5 分钟，重新拉取用户信息刷新角色
      const adminSessionStale = requiresAdmin && sessionValidated && Date.now() - lastValidatedAt > ADMIN_REVALIDATE_INTERVAL
      if (!sessionValidated || !userStore.userInfo?.id || adminSessionStale) {
        try {
          await userStore.getUserInfo()
          sessionValidated = true
          lastValidatedAt = Date.now()
        } catch {
          removeToken()
          userStore.logout({ navigate: false })
          sessionValidated = false
          lastValidatedAt = 0
          next({ name: 'Login', query: { redirect: to.fullPath } })
          return
        }
      }
      if (!userStore.userInfo?.id) {
        removeToken()
        userStore.logout({ navigate: false })
        sessionValidated = false
        lastValidatedAt = 0
        next({ name: 'Login', query: { redirect: to.fullPath } })
        return
      }
      if (requiresAdmin && !userStore.isAdmin) {
        next({ name: 'Dashboard' })
      } else {
        next()
      }
    }
  } else {
    next()
  }
})

export default router
