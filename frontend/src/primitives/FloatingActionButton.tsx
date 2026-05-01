import React, { useCallback, useEffect, useRef } from 'react'
import { colorTokens, spacingTokens } from '../design/tokens'

export interface FloatingActionButtonProps {
  onClick: () => void
  'aria-label': string
  visible?: boolean
  'data-testid'?: string
}

export function FloatingActionButton({
  onClick,
  'aria-label': ariaLabel,
  visible = true,
  'data-testid': testId,
}: FloatingActionButtonProps) {
  const buttonRef = useRef<HTMLButtonElement>(null)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onClick()
      }
    },
    [onClick]
  )

  useEffect(() => {
    if (!visible) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    if (
      previouslyFocused &&
      buttonRef.current &&
      previouslyFocused.closest('.mobile-shell__content')
    ) {
      buttonRef.current.focus()
    }
  }, [visible])

  if (!visible) return null

  return (
    <button
      ref={buttonRef}
      type="button"
      className="fab"
      aria-label={ariaLabel}
      data-testid={testId}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      style={{
        position: 'fixed',
        bottom: `calc(${spacingTokens[8]} + env(safe-area-inset-bottom, 0px))`,
        right: `calc(${spacingTokens[8]} + env(safe-area-inset-right, 0px))`,
        width: '56px',
        height: '56px',
        borderRadius: '50%',
        backgroundColor: colorTokens.accentBlue,
        color: colorTokens.bgPrimary,
        border: 'none',
        boxShadow: '0 4px 12px rgba(0,0,0,0.35)',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 200,
        transition: 'transform 180ms var(--easing-emphasized), opacity 180ms var(--easing-standard)',
      }}
    >
      <svg
        aria-hidden="true"
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    </button>
  )
}
