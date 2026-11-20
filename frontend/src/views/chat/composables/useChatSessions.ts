import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useDialog } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import {
  getConversations,
  deleteConversation,
  type Conversation,
  type ConversationListResponse,
} from '@/api/conversation'
import { apiUnwrap } from '@/utils/apiUnwrap'

export function useChatSessions(getGeneration: () => string = () => '') {
  const router = useRouter()
  const message = useDedupedMessage()
  const dialog = useDialog()
  const { t } = useI18n()
  const chatStore = useChatStore()

  const conversations = ref<Conversation[]>([])
  const isListLoading = ref(false)
  const convLoadError = ref(false)
  const currentPage = ref(1)
  const hasMore = ref(true)
  // 会话列表是 Chat 页自己的抽屉：不再与全局导航折叠状态共用（旧实现点聊天
  // 侧栏开关会把整个主站导航一起收起）。初始收起，桌面宽度由页面 onMounted
  // 的 checkScreenSize 展开。
  const sidebarOpen = ref(false)

  // 会话标题搜索：输入防抖 280ms，查询变更时回到第 1 页；
  // 空白词直接不带 search 参数（后端把空白视为不过滤，前端保持请求形状干净）。
  const conversationSearch = ref('')
  const debouncedSearch = ref('')
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const currentConversationId = computed(() => chatStore.currentConversation?.id)
  let listRequestSeq = 0

  // 刷新/删除后重拉时必须沿用当前查询，否则搜索态会被静默重置为全量。
  const fetchConversations = async (search: string = debouncedSearch.value) => {
    const requestSeq = ++listRequestSeq
    const generation = getGeneration()
    const ownsResponse = () => requestSeq === listRequestSeq && generation === getGeneration()
    const params = {
      page: currentPage.value,
      page_size: 50,
      ...(search ? { search } : {}),
    }
    isListLoading.value = true
    try {
      const res = await getConversations(params)
      if (!ownsResponse()) return
      // 宽容解包（口径见 @/types/common.ts）：真实响应为
      // {success, data: {data: [...], total, page, page_size}} 双层体。
      const page = apiUnwrap<ConversationListResponse | null>(res)
      if (page && Array.isArray(page.data)) {
        const items = page.data
        if (currentPage.value === 1) {
          conversations.value = items
        } else {
          conversations.value = [...conversations.value, ...items]
        }
        hasMore.value = items.length >= 50
      } else {
        hasMore.value = false
      }
      convLoadError.value = false
    } catch {
      if (!ownsResponse()) return
      message.error(t('chat.loadFailed'))
      convLoadError.value = true
    } finally {
      if (requestSeq === listRequestSeq) isListLoading.value = false
    }
  }

  const loadMore = async () => {
    if (!hasMore.value || isListLoading.value) return
    currentPage.value++
    await fetchConversations()
  }

  // 纯函数式防抖：只有最后一次输入会触发请求；clear 走 setConversationSearch('', { immediate: true })
  // 可跳过等待立即恢复全量。竞态仍由 fetchConversations 的 listRequestSeq 兜底。
  const setConversationSearch = (value: string, options: { immediate?: boolean } = {}) => {
    const next = value.trim()
    if (debounceTimer) {
      clearTimeout(debounceTimer)
      debounceTimer = null
    }
    // 输入框是受控组件（:value=conversationSearch），这里必须保留原始输入，
    // 否则用户键入中间的空格会被立即 trim 掉（例如「合同 审查」打不出来）。
    // 请求侧统一用 trim 后的 next；空白词由 ChatSidebar 的 hasSearch(trim) 判定。
    conversationSearch.value = value
    if (options.immediate) {
      debouncedSearch.value = next
      currentPage.value = 1
      void fetchConversations(next)
      return
    }
    debounceTimer = setTimeout(() => {
      debounceTimer = null
      if (next === debouncedSearch.value) return
      debouncedSearch.value = next
      currentPage.value = 1
      void fetchConversations(next)
    }, 280)
  }

  onUnmounted(() => {
    if (debounceTimer) clearTimeout(debounceTimer)
  })

  const handleDeleteConversation = async (id: string) => {
    dialog.warning({
      title: t('common.confirm') || '确认删除',
      content: t('chat.confirmDelete') || '删除后无法恢复，确定要删除此对话吗？',
      positiveText: t('common.confirm'),
      negativeText: t('common.cancel'),
      onPositiveClick: async () => {
        const generation = getGeneration()
        try {
          await deleteConversation(id)
          if (generation !== getGeneration()) return
          message.success(t('common.deleteSuccess'))
          if (currentConversationId.value === id) {
            chatStore.setCurrentConversation(null)
            router.push({ query: {} })
          }
          await fetchConversations()
        } catch {
          if (generation === getGeneration()) message.error(t('common.deleteFailed'))
        }
      }
    })
  }

  return {
    conversations,
    isListLoading,
    convLoadError,
    currentPage,
    hasMore,
    sidebarOpen,
    currentConversationId,
    conversationSearch,
    fetchConversations,
    setConversationSearch,
    loadMore,
    handleDeleteConversation
  }
}
