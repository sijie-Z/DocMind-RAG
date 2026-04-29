import { ref, computed } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import type { AttachedFile } from '@/types/chat'
import { uploadKnowledgeBase, getKnowledgeBase } from '@/api/knowledge'

export function useChatAttachments() {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const attachedFiles = ref<AttachedFile[]>([])
  const fileInputRef = ref<HTMLInputElement | null>(null)
  const attachedFileIds = computed(() => attachedFiles.value.filter(f => f.status === 'done' && f.id).map(f => f.id!))

  const triggerFileUpload = () => {
    const fileInput = document.getElementById('global-chat-file-input') as HTMLInputElement
    if (fileInput) fileInput.click()
  }

  const pollDocumentStatus = async (docId: string, tempFile: AttachedFile) => {
    const maxRetries = 120
    let retries = 0
    while (retries < maxRetries) {
      try {
        const res = await getKnowledgeBase(docId)
        const data = res.data?.data || (res.data as any)
        const status = data.status
        const rawStatus = data.raw_status || status

        const statusDetailMap: Record<string, string> = {
          pending: '等待处理',
          uploaded: '已上传',
          parsing: '解析中',
          parsed: '解析完成',
          indexing: '索引中',
          indexed: '已完成',
          completed: '已完成',
          failed: '解析失败'
        }
        tempFile.statusDetail = statusDetailMap[rawStatus] || statusDetailMap[status] || '处理中'

        if (rawStatus === 'pending' || rawStatus === 'uploaded') {
          tempFile.status = 'uploading'
          tempFile.progress = 10
        } else if (rawStatus === 'parsing') {
          tempFile.status = 'parsing'
          tempFile.progress = 40
        } else if (rawStatus === 'parsed') {
          tempFile.status = 'indexing'
          tempFile.progress = 70
        } else if (rawStatus === 'indexed' || status === 'completed') {
          tempFile.status = 'done'
          tempFile.progress = 100
          tempFile.statusDetail = '✅ 已就绪'
          return
        } else if (status === 'failed') {
          tempFile.status = 'error'
          tempFile.errorMsg = data.parse_error || '解析失败'
          return
        }
      } catch (e) { console.error(e) }
      await new Promise(resolve => setTimeout(resolve, 1000))
      retries++
    }
    if (tempFile.status === 'parsing' || tempFile.status === 'indexing' || tempFile.status === 'uploading') {
      tempFile.status = 'error'
      tempFile.errorMsg = '处理超时，请稍后在知识库中查看状态'
    }
  }

  const handleFileUpload = async (event: Event) => {
    const target = event.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return

    if (file.size > 10 * 1024 * 1024) {
      message.error(t('chat.fileSizeLimit') || '文件大小不能超过 10MB')
      target.value = ''
      return
    }

    const tempFile: AttachedFile = {
      name: file.name,
      status: 'uploading',
      _originalFile: file
    }
    attachedFiles.value.push(tempFile)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', file.name)
      formData.append('description', '来自聊天上传')
      
      const res = await uploadKnowledgeBase(formData)
      const docId = res.data?.data?.id || (res.data as any).id
      if (docId) {
        tempFile.id = String(docId)
        tempFile.status = 'parsing'
        pollDocumentStatus(String(docId), tempFile)
      }
    } catch (error: any) {
      tempFile.status = 'error'
      tempFile.errorMsg = error.message || '上传失败'
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
