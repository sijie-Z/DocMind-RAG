/**
 * 批次十 locale 文案值守护：改 key 容易、改错值不易被发现（复审 N2 回归）。
 *
 * 变异点：
 * - 把 orgSkills.emptyHint 写回「手工提议」承诺（UI 没有该入口）→ 红；
 * - 把回放空态 agentReplay.emptyHint 覆盖成技能文案 → 红。
 */
import { describe, it, expect } from 'vitest'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

const messages = { zh, en, ja, fr } as const

describe('orgSkills / agentReplay 文案值', () => {
  for (const [locale, msg] of Object.entries(messages)) {
    it(`${locale}: 技能空态不承诺不存在的手工提议入口`, () => {
      const text = (msg as any).orgSkills.emptyHint as string
      expect(text).toBeTruthy()
      expect(text).not.toMatch(/手工|手動|manual|manuelle/i)
      expect(text).not.toMatch(/or propose|で手動提案|ou proposez/i)
    })

    it(`${locale}: 关于页 Agent 系统不再宣称「技能学习」为现役能力`, () => {
      const text = (msg as any).about.values.agentSystem as string
      expect(text).toBeTruthy()
      expect(text).not.toMatch(/技能学习|skill learning|スキル学習|apprentissage de compétences/i)
      expect(text).toMatch(/技能库|skill library|スキルライブラリ|bibliothèque de compétences/i)
    })

    it(`${locale}: 回放空态仍是回放说明（未被技能文案覆盖）`, () => {
      const text = (msg as any).agentReplay.emptyHint as string
      expect(text).toBeTruthy()
      expect(text).not.toMatch(/保存为技能|Save as skill|スキルとして保存|Enregistrer comme compétence/i)
      expect(text).toMatch(/回放|replay|リプレイ|replays/i)
    })
  }
})
