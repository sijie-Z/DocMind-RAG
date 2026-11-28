/**
 * Service Worker 导航回退排除表（批次十八）。
 *
 * 公开知识库页面依赖 nginx 对 `/public/kb/` 单独下发的 embed 响应头
 * （CSP frame-ancestors *、无 X-Frame-Options）。Workbox 默认的
 * navigateFallback 会用预缓存的 index.html 响应导航请求，而该缓存响应
 * 携带的是 `/index.html` 的 SAMEORIGIN 头——iframe 会被浏览器拒绝。
 * 因此公开知识库导航必须绕过 SW 回退，走网络拿正确响应头。
 *
 * 保留在独立模块里，便于 vitest 直接断言语义（vite.config.mts 只会被构建加载）。
 */
export const PUBLIC_KB_NAVIGATE_DENYLIST: RegExp[] = [/^\/+public\/kb\//]
