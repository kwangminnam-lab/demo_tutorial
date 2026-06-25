/**
 * 확대/축소 + 이동(pan) 가능한 이미지 뷰어 — AI OCR 원본 미리보기용.
 *
 * 우상단 오버레이 컨트롤(−/배율/+/원래대로) + 드래그로 이미지 이동. 휠로도 확대/축소.
 * 스캔 문서(이미지)를 가까이서 보고 원하는 위치로 끌어 확인할 수 있다. PDF는 브라우저
 * 내장 뷰어를 쓰므로 이 컴포넌트는 이미지 전용.
 */
import { useRef, useState } from 'react'

/** 돋보기+ (확대) */
function IconZoomIn() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.6" />
      <path d="M13.5 13.5 18 18M9 6.5v5M6.5 9h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

/** 돋보기− (축소) */
function IconZoomOut() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.6" />
      <path d="M13.5 13.5 18 18M6.5 9h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

/** 화면 맞춤(원래대로) — 네 모서리 프레임 */
function IconFit() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path
        d="M3 7V4a1 1 0 0 1 1-1h3M17 7V4a1 1 0 0 0-1-1h-3M3 13v3a1 1 0 0 0 1 1h3M17 13v3a1 1 0 0 1-1 1h-3"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  )
}

const MIN = 0.5
const MAX = 5
const STEP = 0.25

export function ImageViewer({ src, alt }: { src: string; alt: string }) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const clamp = (z: number) => Math.min(MAX, Math.max(MIN, Math.round(z * 100) / 100))

  function zoomTo(next: number) {
    const z = clamp(next)
    setScale(z)
    if (z === 1) setOffset({ x: 0, y: 0 }) // 100%로 돌아오면 위치도 초기화.
  }

  function reset() {
    setScale(1)
    setOffset({ x: 0, y: 0 })
  }

  function onPointerDown(e: React.PointerEvent) {
    // 확대 상태에서만 이동(등배율은 이동 의미 없음).
    if (scale <= 1) return
    drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y }
    setDragging(true)
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current) return
    setOffset({
      x: drag.current.ox + (e.clientX - drag.current.x),
      y: drag.current.oy + (e.clientY - drag.current.y),
    })
  }

  function onPointerUp() {
    drag.current = null
    setDragging(false)
  }

  function onWheel(e: React.WheelEvent) {
    if (!e.ctrlKey && !e.metaKey) return // 일반 스크롤과 충돌 방지 — Ctrl/⌘+휠로 줌.
    e.preventDefault()
    zoomTo(scale + (e.deltaY < 0 ? STEP : -STEP))
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#f8f9fa]">
      {/* 우상단 컨트롤 오버레이 — [배율%]  [축소][확대]  [원래대로] */}
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1.5 rounded-md border border-[#e5e7eb] bg-white/95 px-1.5 py-1 text-xs shadow-sm">
        <span className="w-10 text-center tabular-nums font-semibold text-[#6b7280]">
          {Math.round(scale * 100)}%
        </span>
        <span className="h-4 w-px bg-[#e5e7eb]" aria-hidden="true" />
        <div className="flex items-center">
          <button
            type="button"
            aria-label="축소"
            onClick={() => zoomTo(scale - STEP)}
            className="rounded-l border border-[#e5e7eb] px-1.5 py-1 text-[#374151] hover:bg-[#f3f4f6]"
          >
            <IconZoomOut />
          </button>
          <button
            type="button"
            aria-label="확대"
            onClick={() => zoomTo(scale + STEP)}
            className="-ml-px rounded-r border border-[#e5e7eb] px-1.5 py-1 text-[#374151] hover:bg-[#f3f4f6]"
          >
            <IconZoomIn />
          </button>
        </div>
        <button
          type="button"
          aria-label="원래대로"
          title="원래대로(100%)"
          onClick={reset}
          className="rounded border border-[#e5e7eb] px-1.5 py-1 text-[#374151] hover:bg-[#f3f4f6]"
        >
          <IconFit />
        </button>
      </div>

      <div
        className="flex h-full w-full items-center justify-center"
        style={{ cursor: scale > 1 ? (dragging ? 'grabbing' : 'grab') : 'default' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
            transition: dragging ? 'none' : 'transform 0.1s',
          }}
          className="max-h-full max-w-full select-none object-contain p-2"
        />
      </div>
    </div>
  )
}
