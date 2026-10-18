import type { WritableComputedRef } from 'vue'
import { createI18n } from 'vue-i18n'
import zh from './zh'
import { FALLBACK_LANGUAGE, isLanguageCode, type LanguageCode } from '@/utils/languages'

/**
 * 首屏瘦身（E5）：四语包按需加载。
 *
 * 改前：zh/en/ja/fr 四语包全部静态 import，随入口 chunk 一起下发（~160KB 源码），
 * 用户只用一个语言却为四个付费。
 * 改后：仅默认语言 zh 同步内置（同步可读，测试/首帧零等待）；en/ja/fr 由
 * packLoaders 动态 import，打包器拆成独立 chunk，仅在切换/恢复该语言时拉取。
 *
 * 兼容契约：
 *  - `import i18n from '@/locales'` 的同步用法不变（默认 zh 立即就绪）；
 *  - 切换语言统一走 setLanguage()：先就绪词条包再落 locale，切换瞬间不裸键；
 *  - ja/fr 各有 53 个词条缺口（completeness 基线），故 ensureLocale 会连带加载
 *    en 兜底包，缺键仍回退英文而非键名（与改前 fallbackLocale 行为一致）。
 */

/** 词条包结构（四语同构；具体键面由各语言文件与 completeness 用例约束） */
type MessagePack = Record<string, unknown>

const stored = localStorage.getItem('language')

// 默认中文；localStorage 中的非法值（历史脏数据/手改）不注入 locale
const initialLocale: LanguageCode = isLanguageCode(stored) ? stored : 'zh'

const i18n = createI18n({
  legacy: false, // 使用 Composition API
  locale: initialLocale,
  // 诚实降级（ISS-4）：ja/fr 存在词条缺口（主要 chat 域），缺键统一回退 en
  // ——en 100% 完备（locales/__tests__/completeness.test.ts 有硬闸+棘轮保护）。
  // 此前回退 zh：ja/fr 界面缺键处冒出中文，与假名/拉丁界面混杂，观感最差。
  fallbackLocale: FALLBACK_LANGUAGE,
  // 仅内置默认语言包；en/ja/fr 见下方动态加载（运行时由 ensureLocale 补装）
  messages: { zh },
})

/**
 * 落 locale。schema 类型由内置 messages（仅 zh）收窄，而运行时语言集合是
 * LanguageCode（en/ja/fr 动态补装）——在此单点断开编译期窄化，避免每个调用点
 * 各自 cast。
 */
function applyLocale(code: LanguageCode): void {
  ;(i18n.global.locale as WritableComputedRef<LanguageCode>).value = code
}

/** 非内置语言包加载器：动态 import → 独立 chunk，按需拉取 */
const packLoaders: Record<Exclude<LanguageCode, 'zh'>, () => Promise<{ default: MessagePack }>> = {
  en: () => import('./en'),
  ja: () => import('./ja'),
  fr: () => import('./fr'),
}

/** 进行中的加载（幂等 + 并发去重：同一语言包只拉一次） */
const pendingLoads = new Map<LanguageCode, Promise<void>>()

function loadPack(code: LanguageCode): Promise<void> {
  if (code === 'zh') return Promise.resolve()
  const pending = pendingLoads.get(code)
  if (pending) return pending
  const load = packLoaders[code]().then((mod) => {
    // 各语言文件自证键面（completeness 用例守护）；此处只做装载，故按 schema 断言
    i18n.global.setLocaleMessage(code, mod.default as never)
  })
  pendingLoads.set(code, load)
  return load
}

/**
 * 确保某语言词条可用（幂等、并发去重）。
 *
 * ja/fr 必须连带加载 en 兜底包（两语各有 53 个缺口，见 completeness 基线），
 * 否则缺键会裸出键名而非回退英文；zh 自完备、en 即兜底，无需额外加载。
 */
export async function ensureLocale(code: LanguageCode): Promise<void> {
  const tasks = [loadPack(code)]
  if (code !== 'zh' && code !== FALLBACK_LANGUAGE) tasks.push(loadPack(FALLBACK_LANGUAGE))
  await Promise.all(tasks)
}

/** 切换语言序号：并发切换时只让最后一次生效（旧请求晚归不覆盖新选择） */
let switchSeq = 0

/**
 * 切换语言：先就绪词条包，再落 locale + localStorage（避免切换瞬间裸键）。
 */
export async function setLanguage(code: LanguageCode): Promise<void> {
  const seq = ++switchSeq
  await ensureLocale(code)
  if (seq !== switchSeq) return
  applyLocale(code)
  localStorage.setItem('language', code)
}

/**
 * 首屏挂载前调用：保证「当前语言」词条包就绪。
 * 默认 zh 已内置（零等待）；stored 为 en/ja/fr 时在此等待对应动态 chunk，
 * 首帧即为用户语言（不闪默认语言、不裸键）。
 */
export async function prepareCurrentLocale(): Promise<void> {
  const current = i18n.global.locale.value as LanguageCode
  if (isLanguageCode(current)) await ensureLocale(current)
}

export default i18n
