/**
 * Dropdown menu primitive — accessible grouped menu (issue #800, part of #796).
 *
 * Used in the slim top toolstrip (#799) to surface grouped categories that do
 * not warrant a permanent toolstrip slot. Follows the WAI-ARIA menu-button
 * pattern:
 *  - trigger: aria-haspopup="menu", aria-expanded reflects open state;
 *  - menu: role="menu"; items: role="menuitem";
 *  - keyboard: ArrowDown/ArrowUp roving focus (wraps), Enter/Space activate,
 *    Escape closes and restores focus to the trigger;
 *  - click-outside (mousedown) closes;
 *  - first item receives focus on open (focus management / soft focus-trap).
 *
 * LoD: items are flat `{ id, label, onSelect, Icon? }` records — no nested
 * reaching. Reusable across any grouped-category surface (DRY).
 */
import React, {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
} from "react";

export interface DropdownItem {
  /** Stable identifier. */
  id: string;
  /** Visible menu item label. */
  label: string;
  /** Called when the item is activated (click / Enter / Space). */
  onSelect: () => void;
  /** Optional leading icon component. */
  Icon?: (props: { className?: string }) => ReactElement;
  /** Optional active highlight. */
  active?: boolean;
}

export interface DropdownProps {
  /** Trigger button label. */
  label: string;
  /** Flat list of menu items. */
  items: DropdownItem[];
  /** Optional leading icon for the trigger. */
  Icon?: (props: { className?: string }) => ReactElement;
  /** Optional extra class for the trigger button. */
  triggerClassName?: string;
  /**
   * Marks the trigger as the active surface (aria-current="page"), e.g. when
   * the current selection lives inside this menu. Purely presentational.
   */
  triggerActive?: boolean;
}

export function Dropdown({
  label,
  items,
  Icon,
  triggerClassName,
  triggerActive,
}: DropdownProps): ReactElement {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const openMenu = useCallback(() => {
    setActiveIndex(0);
    setOpen(true);
  }, []);

  // Focus the active item whenever the menu is open or the active index moves.
  useEffect(() => {
    if (open) {
      itemRefs.current[activeIndex]?.focus();
    }
  }, [open, activeIndex]);

  // Close on outside mousedown.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (
        !menuRef.current?.contains(t) &&
        !triggerRef.current?.contains(t)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const activate = useCallback(
    (item: DropdownItem) => {
      item.onSelect();
      close();
    },
    [close],
  );

  const handleTriggerKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
    },
    [openMenu],
  );

  const handleMenuKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setActiveIndex((i) => (i + 1) % items.length);
          break;
        case "ArrowUp":
          e.preventDefault();
          setActiveIndex((i) => (i - 1 + items.length) % items.length);
          break;
        case "Home":
          e.preventDefault();
          setActiveIndex(0);
          break;
        case "End":
          e.preventDefault();
          setActiveIndex(items.length - 1);
          break;
        case "Escape":
          e.preventDefault();
          close();
          break;
        case "Enter":
        case " ":
          e.preventDefault();
          activate(items[activeIndex]);
          break;
        case "Tab":
          // Leaving the menu by Tab closes it without stealing focus back.
          setOpen(false);
          break;
        default:
          break;
      }
    },
    [items, activeIndex, close, activate],
  );

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-current={triggerActive ? "page" : undefined}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(false) : openMenu())}
        onKeyDown={handleTriggerKeyDown}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        {Icon ? <Icon /> : null}
        <span>{label}</span>
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 120ms" }}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={handleMenuKeyDown}
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            zIndex: 10000,
            minWidth: 200,
            background: "var(--bg-secondary, #161b22)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            padding: 4,
          }}
        >
          {items.map((item, idx) => {
            const ItemIcon = item.Icon;
            return (
              <button
                key={item.id}
                ref={(el) => {
                  itemRefs.current[idx] = el;
                }}
                type="button"
                role="menuitem"
                tabIndex={idx === activeIndex ? 0 : -1}
                onClick={() => activate(item)}
                onMouseEnter={() => setActiveIndex(idx)}
                aria-current={item.active ? "page" : undefined}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  textAlign: "left",
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "none",
                  background:
                    idx === activeIndex
                      ? "var(--bg-hover, #252d3a)"
                      : "transparent",
                  color: item.active
                    ? "var(--accent-blue, #58a6ff)"
                    : "var(--text-primary, #e6edf3)",
                  fontSize: 13,
                  cursor: "pointer",
                }}
              >
                {ItemIcon ? <ItemIcon /> : null}
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}
