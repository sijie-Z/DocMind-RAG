export interface FileInfo {
  id: number
  filename: string
  fileSize: number
  fileType: string
  uploadStatus: 'pending' | 'processing' | 'completed' | 'failed'
  organizationId: number
  isPublic: boolean
  createdAt: string
  ownerId: number
}

export interface FileUploadResponse {
  fileId: number
  uploadId: string
  chunkSize: number
  totalChunks: number
}

export interface FileChunkUpload {
  uploadId: string
  chunkIndex: number
  chunk: File
}