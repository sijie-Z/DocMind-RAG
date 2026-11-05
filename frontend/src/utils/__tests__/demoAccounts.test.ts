import { describe, expect, it } from 'vitest'
import { parseDemoAccounts } from '../demoAccounts'

describe('parseDemoAccounts', () => {
  it('解析合法账号列表，缺省 label 时用用户名兜底', () => {
    const list = parseDemoAccounts(
      '[{"label":"核验演示","username":"a","password":"p"},{"username":"b","password":"q"}]'
    )
    expect(list).toEqual([
      { label: '核验演示', username: 'a', password: 'p' },
      { label: 'b', username: 'b', password: 'q' }
    ])
  })

  it('未配置、非法 JSON、非数组一律按未配置处理', () => {
    expect(parseDemoAccounts(undefined)).toEqual([])
    expect(parseDemoAccounts('')).toEqual([])
    expect(parseDemoAccounts('{bad')).toEqual([])
    expect(parseDemoAccounts('{"username":"a","password":"p"}')).toEqual([])
  })

  it('丢弃缺少用户名或密码的条目', () => {
    expect(
      parseDemoAccounts(
        '[{"username":"a"},{"password":"p"},{"username":"b","password":"q","label":""}]'
      )
    ).toEqual([{ label: 'b', username: 'b', password: 'q' }])
  })
})
