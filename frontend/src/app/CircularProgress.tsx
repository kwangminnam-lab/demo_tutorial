/**
 * 원형 progress 게이지 + 시간 기반 예상 진행률 훅.
 *
 * 백엔드가 실시간 progress event를 보내지 않는 단계라, 클라이언트가 예상 소요시간을
 * 기준으로 부드럽게 0→90% 채우고 응답 도착 시 100%로 점프한다. 사용자에게 "지금 뭐
 * 하고 있고 언제 끝나는지" 직관을 준다. 실측 진행률(SSE 등)이 생기면 `value` prop만
 * 외부에서 주입해 동일 컴포넌트로 표시할 수 있다.
 */

import { useEffect, useRef, useState } from 'react'

export interface CircularProgressProps {
  /** 진행률 0-100. 미지정이면 인디터미네이트(회전 호) 모드. */
  value?: number
  /** 직경 px. 기본 96. */
  size?: number
  /** stroke 두께 px. 기본 8. */
  thickness?: number
  /** 가운데에 표시할 라벨(보통 % 문자열 또는 단계명). */
  label?: string
  /** 라벨 위 작은 부제. */
  caption?: string
  /** 색상 (CSS). 기본 #1d4ed8. */
  color?: string
  /** 트랙(빈 부분) 색상. 기본 #e5e7eb. */
  trackColor?: string
  /** 접근성 라벨 (aria-label). */
  ariaLabel?: string
}

/**
 * SVG 기반 원형 게이지.
 * - value가 숫자면 결정형 progress (0→100% 부드럽게 stroke-dashoffset 애니메이션).
 * - value가 undefined면 인디터미네이트 (회전하는 호).
 */
export function CircularProgress({
  value,
  size = 96,
  thickness = 8,
  label,
  caption,
  color = '#1d4ed8',
  trackColor = '#e5e7eb',
  ariaLabel,
}: CircularProgressProps) {
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const isIndeterminate = value === undefined

  // 결정형: 0~100을 stroke-dashoffset으로 변환.
  const clamped = Math.max(0, Math.min(100, value ?? 0))
  const offset = circumference * (1 - clamped / 100)

  return (
    <div
      role="progressbar"
      aria-label={ariaLabel ?? '진행 중'}
      aria-valuenow={isIndeterminate ? undefined : Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="relative inline-flex flex-col items-center justify-center"
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className={isIndeterminate ? 'animate-spin' : ''}
      >
        {/* 빈 트랙 */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={trackColor}
          strokeWidth={thickness}
        />
        {/* 진행 호 */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={isIndeterminate ? `${circumference * 0.25} ${circumference}` : circumference}
          strokeDashoffset={isIndeterminate ? 0 : offset}
          style={{
            transition: isIndeterminate
              ? 'none'
              : 'stroke-dashoffset 200ms ease-out',
            transform: 'rotate(-90deg)',
            transformOrigin: '50% 50%',
          }}
        />
      </svg>
      {(label !== undefined || caption !== undefined) && (
        <div
          className="pointer-events-none absolute flex flex-col items-center"
          style={{ width: size, height: size, justifyContent: 'center' }}
        >
          {caption && (
            <span className="text-[10px] font-semibold uppercase tracking-wider text-[#6b7280]">
              {caption}
            </span>
          )}
          {label && (
            <span
              className="text-base font-bold tabular-nums"
              style={{ color }}
            >
              {label}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 시간 기반 예상 진행률 훅.
 *
 * `active=true`면 0%부터 시작해 `estimatedMs`에 걸쳐 90%까지 비선형으로 채운다
 * (초기엔 빠르게, 90%에 가까울수록 느림 — 예상 초과해도 100% 안 됨).
 * 응답이 도착해 호출자가 `active=false`로 바꾸면 즉시 100% 점프 → 1초 뒤 0% 리셋.
 *
 * 백엔드 SSE progress event가 도입되면 이 훅 대신 실측 진행률을 그대로 prop에 꽂으면 된다.
 */
export function useTimedProgress(active: boolean, estimatedMs: number): number {
  const [value, setValue] = useState(0)
  const startRef = useRef<number | null>(null)
  const rafRef = useRef<number | null>(null)
  const completedRef = useRef(false)

  useEffect(() => {
    if (active) {
      // 시작 또는 재시작.
      startRef.current = performance.now()
      completedRef.current = false
      setValue(0)

      const tick = () => {
        if (startRef.current === null) return
        const elapsed = performance.now() - startRef.current
        // 비선형: 1 - e^(-t/τ) 형태로 90% 점근. τ = estimatedMs/2.3 (90%에서 도달).
        const tau = estimatedMs / 2.3
        const progress = 90 * (1 - Math.exp(-elapsed / tau))
        setValue(Math.min(90, progress))
        rafRef.current = requestAnimationFrame(tick)
      }
      rafRef.current = requestAnimationFrame(tick)
      return () => {
        if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      }
    }
    // active=false: 완료 처리.
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (!completedRef.current && startRef.current !== null) {
      // 진행 중이었으면 100% 점프 → 짧게 보여주고 리셋.
      completedRef.current = true
      setValue(100)
      const t = setTimeout(() => setValue(0), 1000)
      return () => clearTimeout(t)
    }
    return undefined
  }, [active, estimatedMs])

  return value
}
