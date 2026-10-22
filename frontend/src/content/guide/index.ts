/**
 * 应用内使用文档注册表。
 *
 * 正文以 Markdown 维护（前端包内 ?raw 导入，走既有 markdownRenderer 渲染），
 * 标题走四语 i18n（help.center.docs.<id>.title）——正文暂不做整篇翻译，
 * 页面上会注明「正文以中文为准」。
 *
 * 新增文档：在对应分组目录下加 .md，随后在此登记并补齐四语标题。
 */
import registerLogin from './getting-started/register-login.md?raw'
import uploadFirstDocument from './getting-started/upload-first-document.md?raw'
import firstQuestion from './getting-started/first-question.md?raw'
import perAgent from './concepts/per-agent.md?raw'
import ragPipeline from './concepts/rag-pipeline.md?raw'
import knowledgeBaseConcept from './concepts/knowledge-base-concept.md?raw'
import organizationMultitenancy from './concepts/organization-multitenancy.md?raw'
import toolsHitl from './concepts/tools-hitl.md?raw'
import chat from './guides/chat.md?raw'
import knowledgeBase from './guides/knowledge-base.md?raw'
import agentWorkbench from './guides/agent-workbench.md?raw'
import workflowEditor from './guides/workflow-editor.md?raw'
import prompts from './guides/prompts.md?raw'
import organizationSettings from './guides/organization-settings.md?raw'
import monitoring from './guides/monitoring.md?raw'
import loginExpiry from './faq/login-expiry.md?raw'
import retrievalNotFound from './faq/retrieval-not-found.md?raw'
import agentApproval from './faq/agent-approval.md?raw'
import orgSwitchSessions from './faq/org-switch-sessions.md?raw'
import cacheDifference from './faq/cache-difference.md?raw'
import healthReadiness from './troubleshooting/health-readiness.md?raw'
import logsRequestId from './troubleshooting/logs-request-id.md?raw'
import feedback from './troubleshooting/feedback.md?raw'

export type GuideGroupId = 'gettingStarted' | 'concepts' | 'guides' | 'faq' | 'troubleshooting'

export interface GuideDoc {
  /** 稳定 id：用于路由 query、DOM data-testid 与测试 */
  id: string
  group: GuideGroupId
  /** 标题 i18n 键（四语） */
  titleKey: string
  /** Markdown 原文（中文） */
  content: string
}

/** 左侧目录分组（父级按当前搜索过滤后传给侧栏） */
export interface GuideNavGroup {
  id: GuideGroupId
  labelKey: string
  docs: GuideDoc[]
}

/** 分组展示顺序（与左侧目录一致） */
export const GUIDE_GROUP_ORDER: GuideGroupId[] = [
  'gettingStarted',
  'concepts',
  'guides',
  'faq',
  'troubleshooting'
]

/** 文档阅读顺序：同时决定「上一篇/下一篇」 */
export const GUIDE_DOCS: GuideDoc[] = [
  {
    id: 'register-login',
    group: 'gettingStarted',
    titleKey: 'help.center.docs.registerLogin.title',
    content: registerLogin
  },
  {
    id: 'upload-first-document',
    group: 'gettingStarted',
    titleKey: 'help.center.docs.uploadFirstDocument.title',
    content: uploadFirstDocument
  },
  {
    id: 'first-question',
    group: 'gettingStarted',
    titleKey: 'help.center.docs.firstQuestion.title',
    content: firstQuestion
  },
  {
    id: 'per-agent',
    group: 'concepts',
    titleKey: 'help.center.docs.perAgent.title',
    content: perAgent
  },
  {
    id: 'rag-pipeline',
    group: 'concepts',
    titleKey: 'help.center.docs.ragPipeline.title',
    content: ragPipeline
  },
  {
    id: 'knowledge-base-concept',
    group: 'concepts',
    titleKey: 'help.center.docs.knowledgeBaseConcept.title',
    content: knowledgeBaseConcept
  },
  {
    id: 'organization-multitenancy',
    group: 'concepts',
    titleKey: 'help.center.docs.organizationMultitenancy.title',
    content: organizationMultitenancy
  },
  {
    id: 'tools-hitl',
    group: 'concepts',
    titleKey: 'help.center.docs.toolsHitl.title',
    content: toolsHitl
  },
  {
    id: 'chat',
    group: 'guides',
    titleKey: 'help.center.docs.chat.title',
    content: chat
  },
  {
    id: 'knowledge-base',
    group: 'guides',
    titleKey: 'help.center.docs.knowledgeBase.title',
    content: knowledgeBase
  },
  {
    id: 'agent-workbench',
    group: 'guides',
    titleKey: 'help.center.docs.agentWorkbench.title',
    content: agentWorkbench
  },
  {
    id: 'workflow-editor',
    group: 'guides',
    titleKey: 'help.center.docs.workflowEditor.title',
    content: workflowEditor
  },
  {
    id: 'prompts',
    group: 'guides',
    titleKey: 'help.center.docs.prompts.title',
    content: prompts
  },
  {
    id: 'organization-settings',
    group: 'guides',
    titleKey: 'help.center.docs.organizationSettings.title',
    content: organizationSettings
  },
  {
    id: 'monitoring',
    group: 'guides',
    titleKey: 'help.center.docs.monitoring.title',
    content: monitoring
  },
  {
    id: 'login-expiry',
    group: 'faq',
    titleKey: 'help.center.docs.loginExpiry.title',
    content: loginExpiry
  },
  {
    id: 'retrieval-not-found',
    group: 'faq',
    titleKey: 'help.center.docs.retrievalNotFound.title',
    content: retrievalNotFound
  },
  {
    id: 'agent-approval',
    group: 'faq',
    titleKey: 'help.center.docs.agentApproval.title',
    content: agentApproval
  },
  {
    id: 'org-switch-sessions',
    group: 'faq',
    titleKey: 'help.center.docs.orgSwitchSessions.title',
    content: orgSwitchSessions
  },
  {
    id: 'cache-difference',
    group: 'faq',
    titleKey: 'help.center.docs.cacheDifference.title',
    content: cacheDifference
  },
  {
    id: 'health-readiness',
    group: 'troubleshooting',
    titleKey: 'help.center.docs.healthReadiness.title',
    content: healthReadiness
  },
  {
    id: 'logs-request-id',
    group: 'troubleshooting',
    titleKey: 'help.center.docs.logsRequestId.title',
    content: logsRequestId
  },
  {
    id: 'feedback',
    group: 'troubleshooting',
    titleKey: 'help.center.docs.feedback.title',
    content: feedback
  }
]
