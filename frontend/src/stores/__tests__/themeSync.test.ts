/**
 * 主题同步判别测试：显式切换才写回 /users/me，服务端水合与同值重设都不写。
 *
 * 现场：layouts 挂载时把 fetchSettings 拉到的 theme 交给 setTheme，setTheme
 * 无条件防抖 PUT /users/me——dashboard 首屏因此出现「GET /users/me + 立即
 * PUT /users/me」的回声写。修复：水合走 persist:false；同值重设直接返回。
 *
 * 变异点：去掉 persist 判断或同值判断 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/user', () => ({
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
  getUserSettings: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateUserSettings: vi.fn().mockResolvedValue({ data: {} }),
}))

import { updateUserProfile } from '@/api/user'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers()
  vi.clearAllMocks()
  setActivePinia(createPinia())
  useUserStore().token = 'tok'
  useAppStore().initTheme()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('主题设置 · 写回边界', () => {
  it('显式切换：500ms 防抖后写回一次', () => {
    const app = useAppStore()

    app.setTheme('dark')
    vi.advanceTimersByTime(500)

    expect(updateUserProfile).toHaveBeenCalledTimes(1)
    expect(updateUserProfile).toHaveBeenCalledWith({ preferences: { theme: 'dark' } })
  })

  it('同值重设：不发写请求', () => {
    const app = useAppStore()

    app.setTheme(app.themeMode)
    vi.advanceTimersByTime(500)

    expect(updateUserProfile).not.toHaveBeenCalled()
  })

  it('服务端水合（persist:false）：只应用主题，不回写', () => {
    const app = useAppStore()

    app.setTheme('light', { persist: false })
    vi.advanceTimersByTime(500)

    expect(app.themeMode).toBe('light')
    expect(updateUserProfile).not.toHaveBeenCalled()
  })
})
