/**
 * 品牌防回归测试（ISS-2 品牌清扫）。
 *
 * 背景：浏览器标签页标题（index.html）与 en/fr 欢迎语曾残留旧品牌
 * "派聪明 / Paicongming"。本测试锁死两类对外可见载体——index.html
 * 与四种语言的全部文案——防止旧品牌文案再次回流。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../locales/en'
import fr from '../locales/fr'
import ja from '../locales/ja'
import zh from '../locales/zh'

const BRAND_RESIDUE = /派聪明|paicongming/i

// vitest 以 frontend/ 为根运行（npm test 在 frontend/ 下执行），index.html 在根下。
// 不用 import.meta.url：vitest 转换后的模块里它不是 file: 协议，fileURLToPath 会抛。
const indexHtml = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8')

describe('品牌防回归：对外文案不得出现旧品牌', () => {
  it('index.html 不含"派聪明 / Paicongming"残留', () => {
    expect(indexHtml).not.toMatch(BRAND_RESIDUE)
  })

  it('index.html 标题使用 DocMind 品牌', () => {
    const title = indexHtml.match(/<title>(.*?)<\/title>/)?.[1] ?? ''
    expect(title).toContain('DocMind')
  })

  const locales: Array<[string, unknown]> = [
    ['en', en],
    ['fr', fr],
    ['ja', ja],
    ['zh', zh],
  ]
  for (const [name, locale] of locales) {
    it(`${name} locale 不含"派聪明 / Paicongming"残留`, () => {
      expect(JSON.stringify(locale)).not.toMatch(BRAND_RESIDUE)
    })
  }
})
