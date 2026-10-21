/**
 * /users 页 i18n 引用四语完整性判别用例（浏览器复验 K2 裸键表头修复）。
 *
 * K2 实锤：表头露出裸键 `profile.email` / `profile.status`（zh/en 语言包缺键，
 * console 实录 [intlify] Not found 'profile.email' key）。同一页表单/弹窗另有
 * 若干无默认值引用（users.initialPassword、profile.placeholder.* 等）同样缺键。
 * 本用例把「页面引用的全部键 × zh/en/ja/fr」逐一断言可解析（非空且非键名本身），
 * 即"grep 验证不再有裸键引用"的可执行版。
 *
 * 变异点：删任一补入键（或改引用键名）→ 对应用例精确红（missing 列表点名）。
 */
import { describe, it, expect } from 'vitest'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

// views/users/index.vue 全部 t('…') 引用（grep 全集，含默认值引用）
const PAGE_REFS = [
  'common.actions',
  'common.cancel',
  'common.confirm',
  'common.delete',
  'common.edit',
  'common.failed',
  'common.loading',
  'common.retry',
  'common.save',
  'common.success',
  'profile.confirmPassword',
  'profile.email',
  'profile.newPassword',
  'profile.nickname',
  'profile.phone',
  'profile.placeholder.email',
  'profile.placeholder.nickname',
  'profile.placeholder.phone',
  'profile.placeholder.username',
  'profile.role',
  'profile.status',
  'profile.username',
  'users.active',
  'users.create',
  'users.deleteConfirmWithName',
  'users.edit',
  'users.inactive',
  'users.initialPassword',
  'users.loadFailed',
  'users.loadFailedDesc',
  'users.noData',
  'users.orgOptionsLoadFailed',
  'users.organizations',
  'users.resetPassword',
  'users.roleAdmin',
  'users.roleUser',
  'users.searchPlaceholder',
  'users.selectOrgs',
  'users.subtitle',
  // T9：h1 改用菜单键（users.title 退役）
  'menu.users',
  'validation.email',
  'validation.passwordLength',
  'validation.passwordMismatch',
  'validation.required',
]

const LOCALES: Array<[string, Record<string, unknown>]> = [
  ['zh', zh],
  ['en', en],
  ['ja', ja],
  ['fr', fr],
]

function resolve(messages: Record<string, unknown>, path: string): unknown {
  let node: unknown = messages
  for (const seg of path.split('.')) {
    if (node && typeof node === 'object' && seg in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[seg]
    } else {
      return undefined
    }
  }
  return node
}

describe('/users 页 i18n 引用四语完整性（K2 裸键表头修复）', () => {
  for (const [name, messages] of LOCALES) {
    it(`${name}: 全部 ${PAGE_REFS.length} 个引用键可解析（非空、非键名本身）`, () => {
      const missing: string[] = []
      for (const ref of PAGE_REFS) {
        const value = resolve(messages, ref)
        if (typeof value !== 'string' || value.length === 0 || value === ref) {
          missing.push(ref)
        }
      }
      expect(missing.join(', '), `${name} 缺失键`).toBe('')
    })
  }
})
