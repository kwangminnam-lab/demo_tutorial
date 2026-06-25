import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Dropzone } from './Dropzone'

const FILE = new File([new Uint8Array([1, 2, 3])], 'doc.pdf', {
  type: 'application/pdf',
})

// Dropzone 루트 div = 숨은 input 의 부모.
function zoneOf(input: HTMLElement): HTMLElement {
  return input.parentElement as HTMLElement
}

describe('Dropzone', () => {
  it('drop 이벤트에서 onFiles를 dataTransfer.files로 호출한다', () => {
    const onFiles = vi.fn()
    render(<Dropzone onFiles={onFiles} ariaLabel="파일 선택" />)

    const zone = zoneOf(screen.getByLabelText('파일 선택'))
    fireEvent.drop(zone, { dataTransfer: { files: [FILE] } })

    expect(onFiles).toHaveBeenCalledTimes(1)
    expect(onFiles.mock.calls[0][0][0]).toBe(FILE)
  })

  it('영역 클릭이 숨은 input을 트리거한다', () => {
    render(<Dropzone onFiles={vi.fn()} ariaLabel="파일 선택" />)

    const input = screen.getByLabelText('파일 선택') as HTMLInputElement
    const clickSpy = vi.spyOn(input, 'click')

    fireEvent.click(zoneOf(input))
    expect(clickSpy).toHaveBeenCalled()
  })

  it('input change에서도 onFiles를 호출한다', () => {
    const onFiles = vi.fn()
    render(<Dropzone onFiles={onFiles} ariaLabel="파일 선택" />)

    fireEvent.change(screen.getByLabelText('파일 선택'), {
      target: { files: [FILE] },
    })
    expect(onFiles).toHaveBeenCalledTimes(1)
  })

  it('빈 drop은 onFiles를 호출하지 않는다', () => {
    const onFiles = vi.fn()
    render(<Dropzone onFiles={onFiles} ariaLabel="파일 선택" />)

    fireEvent.drop(zoneOf(screen.getByLabelText('파일 선택')), {
      dataTransfer: { files: [] },
    })
    expect(onFiles).not.toHaveBeenCalled()
  })

  it('disabled면 drop·클릭이 무시된다', () => {
    const onFiles = vi.fn()
    render(<Dropzone onFiles={onFiles} ariaLabel="파일 선택" disabled />)

    const input = screen.getByLabelText('파일 선택') as HTMLInputElement
    const zone = zoneOf(input)
    const clickSpy = vi.spyOn(input, 'click')

    fireEvent.drop(zone, { dataTransfer: { files: [FILE] } })
    fireEvent.click(zone)

    expect(onFiles).not.toHaveBeenCalled()
    expect(clickSpy).not.toHaveBeenCalled()
  })
})
