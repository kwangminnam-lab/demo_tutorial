import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ImageViewer } from './ImageViewer'

describe('ImageViewer', () => {
  it('기본 100%, 확대/축소 버튼이 배율을 바꾸고 원래대로가 100% 복귀', () => {
    render(<ImageViewer src="data:image/png;base64,AAAA" alt="원본" />)
    expect(screen.getByText('100%', { selector: 'span' })).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('확대'))
    expect(screen.getByText('125%')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('확대'))
    expect(screen.getByText('150%')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('축소'))
    expect(screen.getByText('125%')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('원래대로'))
    expect(screen.getByText('100%', { selector: 'span' })).toBeInTheDocument()
  })

  it('확대 시 이미지에 scale transform이 적용된다', () => {
    render(<ImageViewer src="data:image/png;base64,AAAA" alt="원본" />)
    const img = screen.getByAltText('원본')
    fireEvent.click(screen.getByLabelText('확대'))
    expect(img.style.transform).toContain('scale(1.25)')
  })
})
