#!/usr/bin/env node
/**
 * 设计系统硬约束：UI 源码不得出现紫色系（violet/purple/indigo/fuchsia/pink）
 * 或任何动态 HSL 彩虹色。按色相（HSV）扫描，而不是维护容易漏项的颜色清单。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = new URL('../src', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const EXTS = new Set(['.vue', '.ts', '.css'])
const COLOR_WORD = /\b(purple|violet|indigo|fuchsia|pink)\b/i
const HEX = /#([0-9a-fA-F]{6})\b/g
const RGB = /rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/g
const HSL = /\bhsl\(/i

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue
      out.push(...walk(full))
    } else if (EXTS.has(name.slice(name.lastIndexOf('.')))) {
      out.push(full)
    }
  }
  return out
}

function hue(r, g, b) {
  const rn = r / 255, gn = g / 255, bn = b / 255
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn)
  const d = max - min
  if (d === 0) return { h: 0, s: 0 }
  let h
  if (max === rn) h = ((gn - bn) / d) % 6
  else if (max === gn) h = (bn - rn) / d + 2
  else h = (rn - gn) / d + 4
  h *= 60
  if (h < 0) h += 360
  return { h, s: d / max }
}

const hits = []
for (const file of walk(ROOT)) {
  const text = readFileSync(file, 'utf8')
  const lines = text.split(/\r?\n/)
  lines.forEach((line, i) => {
    const where = `${relative(process.cwd(), file)}:${i + 1}`
    if (COLOR_WORD.test(line) && !/^\s*\/\//.test(line)) {
      hits.push(`${where}  禁用色相关键词：${line.trim().slice(0, 110)}`)
    }
    if (HSL.test(line)) {
      hits.push(`${where}  动态 HSL 颜色禁止使用：${line.trim().slice(0, 110)}`)
    }
    for (const m of line.matchAll(HEX)) {
      const x = m[1]
      const { h, s } = hue(parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16), parseInt(x.slice(4, 6), 16))
      if (h >= 245 && h <= 335 && s > 0.15) {
        hits.push(`${where}  #${x}（hue ${h.toFixed(0)}°，sat ${(s * 100).toFixed(0)}%）：${line.trim().slice(0, 110)}`)
      }
    }
    for (const m of line.matchAll(RGB)) {
      const [r, g, b] = m.slice(1, 4).map(Number)
      const { h, s } = hue(r, g, b)
      if (h >= 245 && h <= 335 && s > 0.15) {
        hits.push(`${where}  rgb(${r},${g},${b})（hue ${h.toFixed(0)}°，sat ${(s * 100).toFixed(0)}%）：${line.trim().slice(0, 110)}`)
      }
    }
  })
}

if (hits.length) {
  console.error(`✗ 发现 ${hits.length} 处紫色系/动态 HSL 颜色：\n`)
  for (const h of hits) console.error('  ' + h)
  process.exit(1)
}
console.log('✓ 无紫色系/动态 HSL 颜色')
