// 通用接口响应类型。
//
// 口径（T11 / 方案 B：放弃全局成功信封，前后端双向收敛）：
// 成功路径历史上混有三种形态，前端统一用 @/utils/apiUnwrap 宽容解包：
//   形态①「裸结构」：响应体即业务体（无信封字段），如
//     GET /notifications/ → { items, total, unread_count, skip, limit }
//   形态②「{data}」：响应体带一层 data（可含 success 等标记），如
//     GET /documents/{id} → { success: true, data: { id, title, … } }
//   形态③「{data:{data}}」：外皮 + 业务体自身再带 data 的分页体，如
//     GET /api/v1/knowledge/ →
//       { success, message, data: { data: [...], total, page, page_size } }
// 错误路径为统一信封（AppError / 422 / 500 处理器）：
//   { success: false, code, message, detail, data: null, request_id? }
export interface ApiResponse<T = any> {
  /** 信封标记（可选——实际端点不强出；apiUnwrap 以此判定外皮） */
  success?: boolean
  code?: number
  message?: string
  /** 错误信封承载的细节（如 FastAPI 422 的 detail 数组） */
  detail?: unknown
  /** 错误信封随附的请求 ID（成功路径一般没有） */
  request_id?: string
  /** 业务体（形态③时为再嵌一层 data 的分页体） */
  data: T
}

/** 形态③双层体：内层 data 为业务列表/对象，其余并列键（total 等）为其元数据。 */
export interface DoubleDataEnvelope<T = unknown, M = Record<string, unknown>> {
  success?: boolean
  message?: string
  data: { data: T } & M
}

/** 成功响应体三形态（裸形态即业务类型 T 本身）。 */
export type ApiBody<T = unknown> = T | ApiResponse<T> | DoubleDataEnvelope<T>

/** 分页数据体——历史遗留类型（当前全仓无导入方）。新代码请使用各 api 模块
 *  自带的分页类型（如 knowledge.ts 的 KnowledgeBaseListResponse）。 */
export interface PaginatedResponse<T = unknown> {
  list: T[]
  total: number
  page: number
  size: number
  pages: number
}
