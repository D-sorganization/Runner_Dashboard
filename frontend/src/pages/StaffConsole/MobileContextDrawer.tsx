/**
 * MobileContextDrawer.tsx — the mobile role-details drawer (SC-D8), extracted from
 * Mobile.tsx for #1804 so it loads the same role context as the desktop pane:
 * provider readiness, schedule, runs and work items from `useRoleContext`.
 */
import React from "react";
import { ContextPane } from "./ContextPane";
import type { StaffRoleItem } from "./types";
import { useRoleContext } from "./useRoleContext";

export interface MobileContextDrawerProps {
  role: StaffRoleItem;
  threadId?: string | null;
  onClose: () => void;
}

export const MobileContextDrawer: React.FC<MobileContextDrawerProps> = ({ role, threadId, onClose }) => {
  const context = useRoleContext(role, threadId);
  return (
    <div
      className="staff-mobile__drawer-overlay"
      data-testid="staff-mobile-context-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="Role Context Details"
      onClick={onClose}
    >
      <div className="staff-mobile__drawer" onClick={(e) => e.stopPropagation()}>
        <div className="staff-mobile__drawer-header">
          <h2 style={{ margin: 0, fontSize: 16 }}>{role.title} Details</h2>
          <button
            type="button"
            className="staff-mobile__drawer-close"
            data-testid="staff-mobile-close-drawer"
            aria-label="Close Details"
            onClick={onClose}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <ContextPane {...context} />
      </div>
    </div>
  );
};
