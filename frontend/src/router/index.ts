import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import Layout from '@/layouts/index.vue'
import { useUserStore } from '@/stores/user'
import { getToken, isTokenExpired, removeToken } from '@/utils/auth'

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
          title: 'menu.dashboard',
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
          icon: 'git-network'
        }
      },
      {
        path: 'workflow/memory',
        name: 'AgentMemory',
        component: () => import('@/views/workflow/memory.vue'),
        meta: {
          title: 'router.agentMemory',
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
        path: 'system-help',
        name: 'SystemHelp',
        component: () => import('@/views/systemHelp/index.vue'),
        meta: {
          title: 'menu.systemHelp',
          icon: 'help'
        }
      },
      {
        path: 'system-about',
        name: 'SystemAbout',
        component: () => import('@/views/systemAbout/index.vue'),
        meta: {
          title: 'menu.systemAbout',
          icon: 'information'
        }
      },
      {
        path: 'notifications',
        name: 'Notifications',
        component: () => import('@/views/notifications/index.vue'),
        meta: {
          title: 'menu.notifications',
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

  // token 过期 → 直接清除，当无 token 处理
  if (token && isTokenExpired()) {
    removeToken()
    userStore.logout()
    token = undefined
    sessionValidated = false
    lastValidatedAt = 0
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
          userStore.logout()
          sessionValidated = false
          lastValidatedAt = 0
          next({ name: 'Login', query: { redirect: to.fullPath } })
          return
        }
      }
      if (!userStore.userInfo?.id) {
        removeToken()
        userStore.logout()
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
