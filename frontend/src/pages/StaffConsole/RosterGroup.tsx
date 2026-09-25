/**
 * RosterGroup.tsx — Collapsible operational tier group for Staff Roster (SC-D3, Issue #1317).
 */

import React from "react";
import type { RosterGroupProps } from "./rosterTypes";
import { RosterRow } from "./RosterRow";

export const RosterGroup: React.FC<RosterGroupProps> = ({
  tier,
  roles,
  selectedRole,
  focusedRole,
  pinnedRoles,
  isCollapsed,
  onToggleCollapse,
  onSelectRole,
  onTogglePin,
}) => {
  const groupSlug = tier.toLowerCase().replace(/\s+/g, "-");
  const testId = `roster-group-${groupSlug}`;

  return (
    <section className="roster-group" data-testid={testId} aria-label={`${tier} group`}>
      <button
        type="button"
        className="roster-group__header"
        onClick={onToggleCollapse}
        aria-expanded={!isCollapsed}
        aria-controls={`group-content-${groupSlug}`}
      >
        <span className="roster-group__chevron" aria-hidden="true">
          {isCollapsed ? "▶" : "▼"}
        </span>
        <span className="roster-group__title">{tier}</span>
        <span className="roster-group__count">({roles.length})</span>
      </button>

      {!isCollapsed && (
        <div id={`group-content-${groupSlug}`} className="roster-group__content" role="group">
          {roles.map((role) => (
            <RosterRow
              key={role.name}
              role={role}
              isSelected={selectedRole === role.name}
              isFocused={focusedRole === role.name}
              isPinned={pinnedRoles.includes(role.name)}
              onSelect={() => onSelectRole(role.name)}
              onTogglePin={() => onTogglePin(role.name)}
            />
          ))}
        </div>
      )}
    </section>
  );
};
