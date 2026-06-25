import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import App from './App'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('App (로그인 제거, ADR-026)', () => {
  it('로그인 화면 없이 바로 메인 셸(네비)을 렌더한다', () => {
    render(<App />)

    // 로그인 폼이 아니라 앱 네비가 바로 보인다.
    expect(
      screen.getByRole('navigation', { name: '주요 메뉴' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: '로그인' })).not.toBeInTheDocument()
  })

  it('주요 메뉴 항목을 모두 렌더한다', () => {
    render(<App />)

    for (const label of ['대시보드', '검색', '챗봇', '문서 비교', '문서 파싱', 'AI OCR']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument()
    }
  })
})
