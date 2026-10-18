/**
 * 导航菜单单一事实源（T5 命名归一）。
 *
 * 定则：同一页面的 **菜单 / 顶栏 / 面包屑 / h1** 四处引用同一个 i18n 键
 * （值以菜单语义为准）。本文件承载"菜单"这一处的声明；顶栏读 route.meta.title
 * （layouts 的 currentRouteTitle），页内 h1 在各自视图里引用同一键。
 *
 * 契约（namingConsistency.test.ts 守护）：
 * - item.routeName 必须是 router 中存在的具名路由（naive-ui 菜单 key=路由 name，
 *   layouts 的 activeKey 高亮依赖此契约，不得破坏）；
 * - item.i18nKey 必须四语（zh/en/ja/fr）存在且非空；
 * - 对应路由的 meta.title 必须 === item.i18nKey；
 * - 已归一页面（见扫描器注册表）的 h1 必须引用同一键。
 *
 * icon 为图标名（字符串），由布局层映射为组件——本文件保持纯数据，
 * 便于测试直接遍历，不拖入 @vicons 组件依赖。
 */

/** 菜单项声明 */
export interface MenuItemDef {
  /** 目标路由 name（也是 naive-ui 菜单 key） */
  routeName: string
  /** 显示名 i18n 键（菜单语义的规范键） */
  i18nKey: string
  /** 图标名（layouts 层 MENU_ICONS 映射） */
  icon: string
}

/** 侧边栏分组声明 */
export interface MenuGroupDef {
  /** 组标题 i18n 键（必填：侧栏按场景域分组，组标题始终渲染） */
  groupI18nKey: string
  /** 仅管理员可见的分组 */
  adminOnly?: boolean
  items: MenuItemDef[]
}

/**
 * 侧边栏菜单（顺序即渲染顺序）：六个场景域。
 *
 * 工作台 / 对话 / 知识库 / 自动化 / 提示词 面向所有登录用户；
 * 管理域名下全部是管理端页面，adminOnly 由布局层按 userStore.isAdmin 过滤。
 * 个人设置等账户级入口在用户下拉菜单（USER_MENU_ITEMS），不占侧栏场景域。
 */
export const SIDEBAR_MENU_GROUPS: MenuGroupDef[] = [
  {
    groupI18nKey: 'menu.groupWorkspace',
    items: [
      { routeName: 'Dashboard', i18nKey: 'menu.home', icon: 'home' },
    ],
  },
  {
    groupI18nKey: 'menu.groupConversation',
    items: [
      // B2（D3 会话入口合并）：独立「历史记录」一级项已移除——会话列表在
      // 对话页侧栏（ChatSidebar）内提供。
      { routeName: 'Chat', i18nKey: 'menu.chat', icon: 'chat' },
    ],
  },
  {
    groupI18nKey: 'menu.groupKnowledge',
    items: [
      { routeName: 'Knowledge', i18nKey: 'menu.knowledge', icon: 'book' },
      { routeName: 'Search', i18nKey: 'menu.search', icon: 'search' },
    ],
  },
  {
    groupI18nKey: 'menu.groupAutomation',
    items: [
      // B2（D3）：独立「智能 Agent」一级项已移除——Agent（PER）入口改为
      // 对话页内的「深度分析（Agent）」模式开关。
      { routeName: 'Workflow', i18nKey: 'menu.workflow', icon: 'git-network' },
      { routeName: 'ScheduledTasks', i18nKey: 'menu.scheduledTasks', icon: 'calendar' },
    ],
  },
  {
    groupI18nKey: 'menu.groupPrompts',
    items: [
      { routeName: 'Prompts', i18nKey: 'menu.prompts', icon: 'terminal' },
    ],
  },
  {
    groupI18nKey: 'menu.groupManagement',
    adminOnly: true,
    items: [
      { routeName: 'Users', i18nKey: 'menu.users', icon: 'people' },
      { routeName: 'Organizations', i18nKey: 'menu.organizations', icon: 'business' },
      { routeName: 'AuditLogs', i18nKey: 'menu.auditLogs', icon: 'document-text' },
      { routeName: 'Monitoring', i18nKey: 'menu.monitoring', icon: 'pulse' },
      { routeName: 'McpServers', i18nKey: 'menu.mcpServers', icon: 'server' },
    ],
  },
]

/** 头部用户下拉项声明 */
export interface UserMenuItemDef {
  /** 下拉动作 key（naive-ui DropdownOption key） */
  actionKey: string
  /** 目标路由 name；纯动作项（如登出）为 null */
  routeName: string | null
  /** 显示名 i18n 键 */
  i18nKey: string
  icon: string
}

/** 头部用户下拉菜单 */
// T9：用户下拉项 key 与路由键空间统一——有路由的项 actionKey === routeName；
// 同时让 Notifications 页拥有 menu.ts 键集内的菜单面归属（meta.activeMenu）。
export const USER_MENU_ITEMS: UserMenuItemDef[] = [
  { actionKey: 'Profile', routeName: 'Profile', i18nKey: 'menu.personalSettings', icon: 'settings' },
  { actionKey: 'Notifications', routeName: 'Notifications', i18nKey: 'menu.notifications', icon: 'notifications' },
  { actionKey: 'SystemHelp', routeName: 'SystemHelp', i18nKey: 'menu.systemHelp', icon: 'help' },
  { actionKey: 'SystemAbout', routeName: 'SystemAbout', i18nKey: 'menu.systemAbout', icon: 'info' },
  { actionKey: 'logout', routeName: null, i18nKey: 'menu.logout', icon: 'logout' },
]
