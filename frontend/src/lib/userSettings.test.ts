/** userSettings 단위 테스트 — 마스킹·해시·저장/로드 결정론. */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  hashPassword,
  loadSettings,
  saveSettings,
  updateSettings,
  verifyPassword,
} from './userSettings'

beforeEach(() => {
  localStorage.clear()
})

describe('hashPassword', () => {
  it('SHA-256 hex digest 길이 64', async () => {
    const h = await hashPassword('hello')
    expect(h).toHaveLength(64)
    expect(/^[0-9a-f]+$/.test(h)).toBe(true)
  })

  it('같은 입력 → 같은 해시 (결정론)', async () => {
    const a = await hashPassword('test')
    const b = await hashPassword('test')
    expect(a).toBe(b)
  })

  it('다른 입력 → 다른 해시', async () => {
    const a = await hashPassword('test1')
    const b = await hashPassword('test2')
    expect(a).not.toBe(b)
  })
})

describe('verifyPassword', () => {
  it('hash가 없으면 항상 통과 (보호 비활성)', async () => {
    expect(await verifyPassword('anything', null)).toBe(true)
  })

  it('hash 일치 시 true', async () => {
    const h = await hashPassword('mypass')
    expect(await verifyPassword('mypass', h)).toBe(true)
  })

  it('hash 불일치 시 false', async () => {
    const h = await hashPassword('mypass')
    expect(await verifyPassword('wrong', h)).toBe(false)
  })
})

describe('save/load/updateSettings', () => {
  it('초기 상태는 기본값', () => {
    const s = loadSettings()
    expect(s.disabledSources).toEqual([])
    expect(s.selectedProvider).toBe('gemma')
    expect(s.profilePasswordHash).toBeNull()
  })

  it('저장 후 로드가 같다', () => {
    saveSettings({
      disabledSources: ['slack'],
      selectedProvider: 'gemma',
      profilePasswordHash: 'abc',
    })
    const s = loadSettings()
    expect(s.disabledSources).toEqual(['slack'])
    expect(s.selectedProvider).toBe('gemma')
    expect(s.profilePasswordHash).toBe('abc')
  })

  it('updateSettings는 다른 필드를 병합·갱신', () => {
    saveSettings({
      disabledSources: [],
      selectedProvider: 'gemma',
      profilePasswordHash: null,
    })
    updateSettings({ disabledSources: ['slack'] })
    const s = loadSettings()
    expect(s.disabledSources).toEqual(['slack'])
    expect(s.selectedProvider).toBe('gemma')  // 미지정 필드 보존
    updateSettings({ selectedProvider: 'gemma' })
    expect(loadSettings().selectedProvider).toBe('gemma')
  })
})
