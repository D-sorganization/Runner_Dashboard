/**
 * ThemeSelector — dropdown component for fleet-wide theme switching.
 *
 * Displays all 13 fleet themes grouped by category with color
 * preview swatches. Supports system preference auto-detection.
 *
 * Addresses: Runner_Dashboard#618 (Color Theme Management)
 */
import React, { useCallback, useState, useRef, useEffect } from 'react';
import {
  FLEET_THEMES,
  getFleetThemesByCategory,
  getFleetThemeDisplayName,
  isFleetThemeDark,
  type FleetThemeId,
} from '../design/fleetThemes';
import type { ThemeMode } from '../hooks/useTheme';

interface ThemeSelectorProps {
  currentMode: ThemeMode;
  onThemeChange: (mode: ThemeMode) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  standard: 'Standard',
  neutral: 'Neutral',
  nature: 'Nature',
  editor: 'Editor / IDE',
  office: 'Office',
  accessibility: 'Accessibility',
};

function ThemeSwatch({ themeId }: { themeId: FleetThemeId }) {
  const def = FLEET_THEMES[themeId];
  if (!def) return null;
  const { bg, accent, text, border } = def.colors;
  return (
    <div
      style={{
        display: 'flex',
        gap: '2px',
        marginLeft: 'auto',
        flexShrink: 0,
      }}
    >
      {[bg, accent, text, border].map((color, i) => (
        <span
          key={i}
          style={{
            width: '12px',
            height: '12px',
            borderRadius: '3px',
            backgroundColor: color,
            border: '1px solid rgba(128,128,128,0.3)',
          }}
        />
      ))}
    </div>
  );
}

export const ThemeSelector: React.FC<ThemeSelectorProps> = ({
  currentMode,
  onThemeChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const categories = getFleetThemesByCategory();

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleSelect = useCallback(
    (mode: ThemeMode) => {
      onThemeChange(mode);
      setIsOpen(false);
    },
    [onThemeChange],
  );

  const currentLabel =
    currentMode === 'system'
      ? '⚙ System'
      : getFleetThemeDisplayName(currentMode);

  return (
    <div
      ref={dropdownRef}
      id="theme-selector"
      style={{ position: 'relative', display: 'inline-block' }}
    >
      <button
        id="theme-selector-toggle"
        className="btn"
        onClick={() => setIsOpen(!isOpen)}
        title="Change theme"
        style={{
          gap: '6px',
          minWidth: '120px',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ fontSize: '13px' }}>{currentLabel}</span>
        <span style={{ fontSize: '10px', opacity: 0.6 }}>
          {isOpen ? '▲' : '▼'}
        </span>
      </button>

      {isOpen && (
        <div
          id="theme-selector-dropdown"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            right: 0,
            width: '280px',
            maxHeight: '420px',
            overflowY: 'auto',
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            borderRadius: '10px',
            boxShadow: '0 12px 40px rgba(0,0,0,0.3)',
            zIndex: 9999,
            padding: '6px 0',
          }}
        >
          {/* System option */}
          <button
            id="theme-option-system"
            onClick={() => handleSelect('system')}
            style={{
              ...optionStyle,
              background:
                currentMode === 'system'
                  ? 'var(--bg-hover)'
                  : 'transparent',
              fontWeight: currentMode === 'system' ? 600 : 400,
            }}
          >
            <span>⚙ System</span>
          </button>

          {/* Fleet themes by category */}
          {Object.entries(categories).map(([category, themeIds]) => (
            <React.Fragment key={category}>
              <div
                style={{
                  padding: '8px 14px 4px',
                  fontSize: '10px',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  color: 'var(--text-muted)',
                }}
              >
                {CATEGORY_LABELS[category] ?? category}
              </div>
              {themeIds.map((id) => (
                <button
                  key={id}
                  id={`theme-option-${id}`}
                  onClick={() => handleSelect(id)}
                  style={{
                    ...optionStyle,
                    background:
                      currentMode === id
                        ? 'var(--bg-hover)'
                        : 'transparent',
                    fontWeight: currentMode === id ? 600 : 400,
                  }}
                >
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    {isFleetThemeDark(id) ? '🌙' : '☀️'}
                    {getFleetThemeDisplayName(id)}
                  </span>
                  <ThemeSwatch themeId={id} />
                </button>
              ))}
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
};

const optionStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  width: '100%',
  padding: '7px 14px',
  border: 'none',
  cursor: 'pointer',
  fontSize: '13px',
  color: 'var(--text-primary)',
  transition: 'background 0.1s',
  textAlign: 'left',
};
