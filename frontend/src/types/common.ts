// 通用接口响应类型
export interface ApiResponse<T = any> {
  code: number;      // 👈 加上这个，红线就消了
  message: string;
  data: T;           // 这里放具体的数据（用户、文档等）
  success?: boolean; // 可选，有些后端习惯带这个
}

// 分页响应数据类型
export interface PaginatedResponse<T = any> {
  list: T[];         // 有时候叫 items，有时候叫 list，看你后端怎么回
  total: number;
  page: number;
  size: number;
  pages: number;
}
