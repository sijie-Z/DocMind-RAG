import { ref, computed, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import type { AttachedFile } from '@/types/chat'
import { uploadKnowledgeBase, getKnowledgeBase } from '@/api/knowledge'
import { parseUpload } from '@/api/chat'
import { apiUnwrap } from '@/utils/apiUnwrap'

export function useChatAttachments() {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const attachedFiles = ref<AttachedFile[]>([])
  const fileInputRef = ref<HTMLInputElement | null>(null)
  const attachedFileIds = computed(() => attachedFiles.value.filter(f => f.status === 'done' && f.id).map(f => f.id!))

  // 附件轮询中止标志：组件卸载后置位，轮询循环检测到立即退出
  let pollAborted = false

  const triggerFileUpload = () => {
    const fileInput = document.getElementById('global-chat-file-input') as HTMLInputElement
    if (fileInput) fileInput.click()
  }

  const pollDocumentStatus = async (docId: string, tempFile: AttachedFile) => {
    const maxRetries = 120
    let retries = 0
    while (retries < maxRetries) {
      if (pollAborted) return
      try {
        const res = await getKnowledgeBase(docId)
        // 宽容解包：{success, data} 信封 → 业务体；裸形态原样返回。
        const data = apiUnwrap<Record<string, unknown>>(res) || (res.data as Record<string, unknown>)
        const status = data.status
        const rawStatus = data.raw_status || status

        // /documents/{id} 的 status/raw_status 值域为六值
        // pending/uploaded/parsing/parsed/indexed/failed（documents.py:295-310
        // 与 models/document.py:15-22 枚举一致）——不存在 'completed'（那是
        // 知识库「列表」端点的展示映射），旧 `|| status === 'completed'` 为死分支
        // 且 failed 判定误读展示键，统一按 rawStatus 全键域判断（C6）
        const statusDetailMap: Record<string, string> = {
          pending: t('chat.filePending'),
          uploaded: t('chat.fileUploaded'),
          parsing: t('chat.fileParsing'),
          parsed: t('chat.fileParsed'),
          indexing: t('chat.fileIndexing'),
          indexed: t('chat.fileIndexed'),
          failed: t('chat.fileFailed')
        }
        tempFile.statusDetail = statusDetailMap[rawStatus] || statusDetailMap[status] || t('chat.fileProcessing')

        if (rawStatus === 'pending' || rawStatus === 'uploaded') {
          tempFile.status = 'uploading'
          tempFile.progress = 10
        } else if (rawStatus === 'parsing') {
          tempFile.status = 'parsing'
          tempFile.progress = 40
        } else if (rawStatus === 'parsed') {
          tempFile.status = 'indexing'
          tempFile.progress = 70
        } else if (rawStatus === 'indexed') {
          tempFile.status = 'done'
          tempFile.progress = 100
          tempFile.statusDetail = t('chat.fileReady')
          return
        } else if (rawStatus === 'failed') {
          tempFile.status = 'error'
          tempFile.errorMsg = data.parse_error || t('chat.fileFailed')
          return
        }
      } catch { /* ignore */ }
      await new Promise(resolve => setTimeout(resolve, 1000))
      retries++
    }
    if (tempFile.status === 'parsing' || tempFile.status === 'indexing' || tempFile.status === 'uploading') {
      tempFile.status = 'error'
      tempFile.errorMsg = t('chat.fileTimeout')
    }
  }

  onUnmounted(() => {
    // 离开页面时停止轮询，避免组件卸载后仍每秒请求直至 120 次上限
    pollAborted = true
  })

  const handleFileUpload = async (event: Event) => {
    const target = event.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return

    if (file.size > 10 * 1024 * 1024) {
      message.error(t('chat.fileSizeLimit'))
      target.value = ''
      return
    }

    const tempFile: AttachedFile = {
      name: file.name,
      status: 'uploading',
      _originalFile: file,
      progress: 5,
      statusDetail: t('chat.fileParsing'),
    }
    attachedFiles.value.push(tempFile)

    // 策略：先尝试即时解析（parse-upload），快速获取文本作为上下文
    // 如果即时解析失败，回退到传统的知识库上传 + 轮询
    try {
      const result = await parseUpload(file)
      if (result && result.content) {
        tempFile.status = 'done'
        tempFile.progress = 100
        tempFile.statusDetail = t('chat.fileReady')
        tempFile.parsedContent = result.content
        return
      }
    } catch {
      // parse-upload 解析失败（如不支持的文件格式），回退到知识库上传
    }

    // Fallback: 走传统知识库上传 + 轮询等待索引
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', file.name)
      formData.append('description', t('chat.fileFromChat'))

      const res = await uploadKnowledgeBase(formData)
      const uploadBody = res.data as { success?: boolean; message?: string } | undefined
      if (uploadBody?.success === false) {
        // issue #301 批 D（#23）：文件已保存但处理任务启动失败（后端已把 doc
        // 标为 failed，success=false）。如实报错，不再进入 parsing 轮询假等待。
        throw new Error(uploadBody.message || t('chat.fileUploadFailed'))
      }
      const docId = apiUnwrap<{ id?: unknown }>(res)?.id || (res.data as Record<string, unknown>).id
      if (docId) {
        tempFile.id = String(docId)
        tempFile.status = 'parsing'
        pollDocumentStatus(String(docId), tempFile)
      }
    } catch (error: any) {
      tempFile.status = 'error'
      tempFile.errorMsg = error.message || t('chat.fileUploadFailed')
    } finally {
      target.value = ''
    }
  }

  const removeAttachment = (index: number) => {
    attachedFiles.value.splice(index, 1)
  }

  return {
    attachedFiles,
    attachedFileIds,
    fileInputRef,
    triggerFileUpload,
    handleFileUpload,
    removeAttachment
  }
}
