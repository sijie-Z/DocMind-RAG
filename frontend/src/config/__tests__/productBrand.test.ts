/**
 * 品牌位一致性守卫：浏览器标签 / PWA 名称 / 登录页品牌语必须说同一件事。
 *
 * 现场（2026-11-11）：产品已经全面转向「文档问答与合同核验」，但
 * `index.html` 标题、PWA manifest 名称、关于页仍写着「AI知识库 / 智能知识库 /
 * 企业级 RAG 知识库」。这些位置没人测、也没进任何文档数字门禁，于是漂了整整
 * 一个产品周期。本用例把它们钉在一起：三处必须同时包含当前品牌语，且不得
 * 残留已下线的旧品牌词。
 *
 * 变异：把 index.html 标题改回「AI知识库系统」→ 第一个用例红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const FRONTEND_ROOT = process.cwd()
const read = (relPath: string): string => readFileSync(resolve(FRONTEND_ROOT, relPath), 'utf-8')

const BRAND = '文档问答与合同核验'
const RETIRED = /AI知识库|智能知识库|企业级 RAG 知识库/

describe('品牌位一致性（标签页 / PWA / 登录页）', () => {
  const sources = {
    'index.html': read('index.html'),
    'vite.config.mts': read('vite.config.mts'),
    'locales/zh.ts': read('src/locales/zh.ts'),
  }

  it(`三处品牌语都包含「${BRAND}」`, () => {
    for (const [file, text] of Object.entries(sources)) {
      expect(text, file).toContain(BRAND)
    }
  })

  it('标签页与 PWA 不再残留已下线的旧品牌词', () => {
    for (const file of ['index.html', 'vite.config.mts'] as const) {
      expect(sources[file], file).not.toMatch(RETIRED)
    }
  })
})
