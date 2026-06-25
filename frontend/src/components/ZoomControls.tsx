/**
 * 미리보기 확대/축소 컨트롤 — 0.5×~3× 범위, 25% 단위. 파싱 원본 프리뷰·추출 근거
 * 프리뷰에서 공용으로 쓴다. zoom 상태는 호출부가 보유하고, 적용(이미지 width 스케일)은
 * 호출부가 한다 — 이 컴포넌트는 버튼 UI + clamp만 담당한다.
 */
export function ZoomControls({
  zoom,
  onZoom,
}: {
  zoom: number
  onZoom: (zoom: number) => void
}) {
  const clamp = (z: number) => Math.min(3, Math.max(0.5, Math.round(z * 100) / 100))
  return (
    <div className="flex items-center gap-1 text-xs">
      <button
        type="button"
        aria-label="축소"
        onClick={() => onZoom(clamp(zoom - 0.25))}
        className="rounded border border-[#d1d5db] px-2 py-0.5 font-bold text-[#374151] hover:bg-[#f3f4f6]"
      >
        −
      </button>
      <span className="w-10 text-center tabular-nums text-[#6b7280]">
        {Math.round(zoom * 100)}%
      </span>
      <button
        type="button"
        aria-label="확대"
        onClick={() => onZoom(clamp(zoom + 0.25))}
        className="rounded border border-[#d1d5db] px-2 py-0.5 font-bold text-[#374151] hover:bg-[#f3f4f6]"
      >
        +
      </button>
      <button
        type="button"
        aria-label="원래대로"
        onClick={() => onZoom(1)}
        className="rounded border border-[#d1d5db] px-2 py-0.5 font-semibold text-[#374151] hover:bg-[#f3f4f6]"
      >
        100%
      </button>
    </div>
  )
}
