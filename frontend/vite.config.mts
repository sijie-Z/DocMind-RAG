import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { resolve, dirname } from 'path'
import { readFileSync, existsSync } from 'fs'
import { fileURLToPath } from 'url'
import type { Plugin } from 'vite'

const __dirname = dirname(fileURLToPath(import.meta.url))

const BACKEND_PORT_FILE = resolve(__dirname, '..', 'backend', '.backend-port')
const DEFAULT_BACKEND_PORT = 8010

function getBackendPort(): number {
  try {
    if (existsSync(BACKEND_PORT_FILE)) {
      return parseInt(readFileSync(BACKEND_PORT_FILE, 'utf-8').trim(), 10)
    }
  } catch {}
  return DEFAULT_BACKEND_PORT
}

/** Small plugin that exposes the backend port at runtime for tooling */
function backendPortPlugin(): Plugin {
  return {
    name: 'backend-port',
    configureServer(server) {
      // Make the backend port available via a virtual endpoint
      server.middlewares.use('/__backend_port', (_req, res) => {
        res.setHeader('Content-Type', 'text/plain')
        res.end(String(getBackendPort()))
      })
    },
  }
}

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS(),
    backendPortPlugin(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'apple-touch-icon.png', 'mask-icon.svg'],
      manifest: {
        name: 'DocMind 智能知识库',
        short_name: 'DocMind',
        description: '企业级 RAG 知识库系统，支持文档解析、智能问答、语义检索',
        theme_color: '#3b82f6',
        background_color: '#ffffff',
        display: 'standalone',
        scope: '/',
        start_url: '/',
        icons: [
          {
            src: 'pwa-192x192.svg',
            sizes: '192x192',
            type: 'image/svg+xml'
          },
          {
            src: 'pwa-512x512.svg',
            sizes: '512x512',
            type: 'image/svg+xml'
          },
          {
            src: 'pwa-512x512.svg',
            sizes: '512x512',
            type: 'image/svg+xml',
            purpose: 'any maskable'
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-cache',
              expiration: {
                maxEntries: 10,
                maxAgeSeconds: 60 * 60 * 24 * 365
              },
              cacheableResponse: {
                statuses: [0, 200]
              }
            }
          },
          {
            urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'gstatic-fonts-cache',
              expiration: {
                maxEntries: 10,
                maxAgeSeconds: 60 * 60 * 24 * 365
              },
              cacheableResponse: {
                statuses: [0, 200]
              }
            }
          },
          {
            urlPattern: /\/api\/.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: {
                maxEntries: 100,
                maxAgeSeconds: 60 * 60
              },
              cacheableResponse: {
                statuses: [0, 200]
              }
            }
          }
        ]
      }
    })
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    proxy: {
      '/api/v1/notifications/ws': {
        target: `ws://127.0.0.1:${getBackendPort()}`,
        ws: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${getBackendPort()}`,
        ws: true,
        rewrite: (path) => path.replace(/^\/ws/, '/api/v1/chat/ws'),
      },
      '/api': `http://127.0.0.1:${getBackendPort()}`,
      '/static': `http://127.0.0.1:${getBackendPort()}`,
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true
      }
    },
    rollupOptions: {
      output: {
        chunkFileNames: 'assets/js/[name]-[hash].js',
        entryFileNames: 'assets/js/[name]-[hash].js',
        assetFileNames: 'assets/[ext]/[name]-[hash].[ext]',
        manualChunks(id) {
            if (!id.includes('node_modules')) return
            const segment = id.split('node_modules/')[1] ?? ''
            const pkg = segment.split('/')[0] ?? ''
            if (pkg === 'vue' || pkg === 'vue-router' || pkg === 'pinia' || pkg.startsWith('@vue')) {
                return 'vue'
            }
            if (pkg === 'naive-ui' || pkg === '@css-render' || pkg === 'vooks' || pkg === 'treemate' || pkg === 'seemly' || pkg === 'vdirs') {
                return 'naive-ui'
            }
            if (pkg === 'echarts' || pkg === 'zrender') {
                return 'charts'
            }
            if (pkg === 'markdown-it' || pkg === 'highlight.js' || pkg === 'katex' || pkg === 'dompurify') {
                return 'markdown'
            }
            if (pkg === '@vueuse' || pkg === '@vueuse/core') {
                return 'vueuse'
            }
            if (pkg === 'dayjs') {
                return 'dayjs'
            }
            if (pkg === 'lodash' || pkg === 'lodash-es') {
                return 'lodash'
            }
            if (pkg === 'vue-virtual-scroller' || pkg === 'vue-observe-visibility' || pkg === 'vue-resize') {
                return 'virtual-scroller'
            }
            if (pkg === '@vue-flow' || pkg.startsWith('@vue-flow/')) {
                return 'vue-flow'
            }
            if (pkg === 'axios') {
                return 'axios'
            }
            if (pkg === 'qrcode') {
                return 'qrcode'
            }
            return undefined
        }
      }
    }
  }
})
