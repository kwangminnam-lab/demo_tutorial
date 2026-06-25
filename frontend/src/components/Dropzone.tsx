/**
 * 공용 드래그앤드랍 + 클릭 업로드 영역.
 *
 * ParsePage 의 DropField 로직을 일반화한 컴포넌트. 영역에 파일을 드랍하거나
 * 클릭하면 숨은 `<input type="file">` 이 열린다. 시각 스타일(테두리·패딩·정렬)은
 * `className` 으로 호출측이 정의하고, 드래그 하이라이트/커서/비활성 상태만 여기서 입힌다.
 *
 * accept 는 클릭 파일 다이얼로그에만 적용된다 — 브라우저는 드롭에 accept 를 강제하지
 * 않으므로, 드랍된 파일은 거르지 않고 그대로 `onFiles` 로 넘겨 호출측의 기존 검증
 * 흐름(서버 415·메타 검증 등)을 타게 한다(조용한 누락 금지).
 */

import {
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from 'react'

export function Dropzone({
  onFiles,
  accept,
  multiple = false,
  label,
  disabled = false,
  ariaLabel = '파일 선택',
  className = '',
}: {
  onFiles: (files: FileList | File[]) => void
  accept?: string
  multiple?: boolean
  label?: ReactNode
  disabled?: boolean
  ariaLabel?: string
  className?: string
}) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function emit(files: FileList | null) {
    if (!files || files.length === 0) return
    onFiles(files)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    emit(event.dataTransfer.files)
  }
  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    if (!disabled) setDragging(true)
  }
  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
  }
  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    emit(event.target.files)
  }

  return (
    <div
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={() => {
        if (!disabled) inputRef.current?.click()
      }}
      className={`transition-colors ${
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
      } ${dragging ? 'ring-2 ring-inset ring-[#1d4ed8]' : ''} ${className}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        aria-label={ariaLabel}
        disabled={disabled}
        onChange={handleChange}
        className="hidden"
      />
      {label}
    </div>
  )
}
