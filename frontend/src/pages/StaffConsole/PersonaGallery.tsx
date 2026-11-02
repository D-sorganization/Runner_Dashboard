/**
 * PersonaGallery.tsx — Dedicated mobile Persona gallery with persisted ordering and presentation editor (#3502, #3499).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StaffRoleItem } from "./types";
import { PersonaAvatar } from "./PersonaAvatar";
import { PersonaEditor } from "./PersonaEditor";
import {
  fetchPresentationPrefs,
  savePresentationPrefs,
  orderRoles,
  type PresentationPrefsResponse,
} from "./personaGalleryUtils";
import "./personaGallery.css";

export interface PersonaGalleryProps {
  roles: StaffRoleItem[];
  selectedRoleId?: string;
  onSelectRole: (roleId: string) => void;
  isLoading?: boolean;
  className?: string;
  initialPrefs?: PresentationPrefsResponse;
  onPrefsSaved?: (prefs: PresentationPrefsResponse) => void;
}

export const PersonaGallery: React.FC<PersonaGalleryProps> = ({
  roles,
  onSelectRole,
  isLoading = false,
  className = "",
  initialPrefs,
  onPrefsSaved,
}) => {
  const [prefs, setPrefs] = useState<PresentationPrefsResponse | null>(initialPrefs ?? null);
  const [activeOrderNames, setActiveOrderNames] = useState<string[]>(() =>
    initialPrefs?.role_order ? [...initialPrefs.role_order] : roles.map((r) => r.name),
  );
  const [persistedOrderNames, setPersistedOrderNames] = useState<string[]>(() =>
    initialPrefs?.role_order ? [...initialPrefs.role_order] : roles.map((r) => r.name),
  );
  const [isEditing, setIsEditing] = useState(false);
  const [editingPersonaRole, setEditingPersonaRole] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [draggingRole, setDraggingRole] = useState<string | null>(null);
  const touchStartYRef = useRef<number | null>(null);

  useEffect(() => {
    if (initialPrefs) return;
    let active = true;
    fetchPresentationPrefs()
      .then((loaded) => {
        if (!active) return;
        setPrefs(loaded);
        const resolved = loaded.role_order.length > 0 ? loaded.role_order : roles.map((r) => r.name);
        setPersistedOrderNames(resolved);
        setActiveOrderNames(resolved);
      })
      .catch(() => {});
    return () => { active = false; };
  }, [initialPrefs, roles]);

  useEffect(() => {
    if (!isEditing && roles.length > 0) {
      const knownRoleNames = new Set(roles.map((r) => r.name));
      const filtered = persistedOrderNames.filter((name) => knownRoleNames.has(name));
      for (const r of roles) {
        if (!filtered.includes(r.name)) filtered.push(r.name);
      }
      setActiveOrderNames(filtered);
    }
  }, [roles, persistedOrderNames, isEditing]);

  const orderedAllRoles = useMemo(() => orderRoles(roles, activeOrderNames), [roles, activeOrderNames]);

  const visibleRoles = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return orderedAllRoles;
    return orderedAllRoles.filter(
      (r) => r.title.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || (r.summary && r.summary.toLowerCase().includes(q)),
    );
  }, [orderedAllRoles, searchQuery]);

  const moveRoleInCompleteList = useCallback((roleName: string, direction: -1 | 1) => {
    const currentNames = [...activeOrderNames];
    const fromIdx = currentNames.indexOf(roleName);
    if (fromIdx === -1) return;
    const toIdx = fromIdx + direction;
    if (toIdx < 0 || toIdx >= currentNames.length) return;
    const [removed] = currentNames.splice(fromIdx, 1);
    currentNames.splice(toIdx, 0, removed);
    setActiveOrderNames(currentNames);
    const targetRole = roles.find((r) => r.name === roleName);
    setAnnouncement(`Moved ${targetRole?.title || roleName} to position ${toIdx + 1} of ${currentNames.length}`);
  }, [activeOrderNames, roles]);

  const handleCancelEdit = () => {
    setActiveOrderNames([...persistedOrderNames]);
    setIsEditing(false);
    setSaveError(null);
    setAnnouncement("Reordering cancelled. Order restored to previous saved state.");
  };

  const handleSaveOrder = async () => {
    setIsSaving(true);
    setSaveError(null);
    try {
      const updated = await savePresentationPrefs({
        expected_revision: prefs ? prefs.revision : 0,
        role_order: activeOrderNames,
        display_label: prefs ? prefs.display_label : "",
        avatar_overrides: prefs ? prefs.avatar_overrides : {},
      });
      setPrefs(updated);
      setPersistedOrderNames([...updated.role_order]);
      setActiveOrderNames([...updated.role_order]);
      setIsEditing(false);
      setAnnouncement("Persona order saved successfully.");
      onPrefsSaved?.(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveError(msg.includes("409") ? "Conflict: preferences were updated elsewhere. Please reload." : (msg || "Failed to save order."));
    } finally {
      setIsSaving(false);
    }
  };

  if (editingPersonaRole) {
    const activePrefs: PresentationPrefsResponse = prefs ?? { operator_id: "", display_label: "", role_order: activeOrderNames, avatar_overrides: {}, revision: 0, updated_at: "" };
    return (
      <div className={`persona-gallery ${className}`} data-testid="persona-gallery-editor-view">
        <PersonaEditor roles={roles} selectedRoleName={editingPersonaRole} onSelectRoleName={setEditingPersonaRole} prefs={activePrefs} onPrefsSaved={(u) => { setPrefs(u); onPrefsSaved?.(u); }} onClose={() => setEditingPersonaRole(null)} />
      </div>
    );
  }

  return (
    <div className={`persona-gallery ${className}`} data-testid="persona-gallery">
      <div className="sr-only" role="status" aria-live="polite">{announcement}</div>
      <div className="persona-gallery__header">
        <div className="persona-gallery__search-wrapper">
          <input type="search" className="persona-gallery__search" placeholder="Filter personas…" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} aria-label="Filter personas" />
        </div>
        <div className="persona-gallery__actions">
          {!isEditing ? (
            <button type="button" className="persona-gallery__btn" onClick={() => { setIsEditing(true); setAnnouncement("Entered edit mode."); }} data-testid="persona-gallery-edit-btn">Edit order</button>
          ) : (
            <>
              <button type="button" className="persona-gallery__btn persona-gallery__btn--primary" onClick={handleSaveOrder} disabled={isSaving} data-testid="persona-gallery-save-btn">{isSaving ? "Saving..." : "Save order"}</button>
              <button type="button" className="persona-gallery__btn" onClick={handleCancelEdit} disabled={isSaving} data-testid="persona-gallery-cancel-btn">Cancel</button>
            </>
          )}
        </div>
      </div>
      {saveError && (
        <div className="persona-gallery__error" role="alert" data-testid="persona-gallery-error">
          <span>{saveError}</span>
          <button type="button" className="persona-gallery__btn" onClick={handleSaveOrder} disabled={isSaving}>Retry</button>
        </div>
      )}
      {isLoading ? (
        <div className="staff-mobile__skeleton" data-testid="persona-gallery-loading" />
      ) : (
        <div className="persona-gallery__list" role="list" data-testid="persona-gallery-list">
          {visibleRoles.map((role) => {
            const roleIdx = activeOrderNames.indexOf(role.name);
            const isFirst = roleIdx <= 0;
            const isLast = roleIdx >= activeOrderNames.length - 1;
            const isDragging = draggingRole === role.name;
            const effectiveAvatar = prefs?.avatar_overrides[role.name] || role.avatar;
            return (
              <div
                key={role.name}
                role="listitem"
                className={`persona-card ${isEditing ? "persona-card--editing" : ""} ${isDragging ? "persona-card--dragging" : ""}`}
                data-testid={`persona-card-${role.name}`}
                tabIndex={!isEditing ? 0 : undefined}
                onClick={() => { if (!isEditing) onSelectRole(role.name); }}
                onKeyDown={(e) => { if (!isEditing && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSelectRole(role.name); } }}
              >
                {isEditing && (
                  <div
                    className="persona-gallery__drag-handle"
                    role="button"
                    tabIndex={0}
                    aria-label={`Drag to reorder ${role.title}`}
                    data-testid={`drag-handle-${role.name}`}
                    onTouchStart={(e) => { touchStartYRef.current = e.touches[0].clientY; setDraggingRole(role.name); }}
                    onTouchMove={(e) => {
                      if (touchStartYRef.current === null) return;
                      const diff = e.touches[0].clientY - touchStartYRef.current;
                      if (Math.abs(diff) > 40) {
                        moveRoleInCompleteList(role.name, diff > 0 ? 1 : -1);
                        touchStartYRef.current = e.touches[0].clientY;
                      }
                    }}
                    onTouchEnd={() => { touchStartYRef.current = null; setDraggingRole(null); }}
                    onKeyDown={(e) => {
                      if (e.key === "ArrowUp") { e.preventDefault(); moveRoleInCompleteList(role.name, -1); }
                      else if (e.key === "ArrowDown") { e.preventDefault(); moveRoleInCompleteList(role.name, 1); }
                    }}
                  >
                    ⋮⋮
                  </div>
                )}
                <PersonaAvatar variant="roster" roleName={role.name} initial={role.title ? role.title.charAt(0) : "B"} avatarUrl={effectiveAvatar} isAutoRoute={role.name === "barb"} />
                <div className="persona-card__content">
                  <div className="persona-card__header-line">
                    <span className="persona-card__title">{role.title}</span>
                    <span className="persona-card__canonical-id">{role.name}</span>
                    {role.caller_unread_count ? <span className="staff-mobile__unread-badge">{role.caller_unread_count}</span> : null}
                  </div>
                  {role.summary && <div className="persona-card__summary">{role.summary}</div>}
                </div>
                {!isEditing && (
                  <button type="button" className="persona-card__edit-btn" aria-label={`Edit ${role.title} presentation`} onClick={(e) => { e.stopPropagation(); setEditingPersonaRole(role.name); }} data-testid={`edit-persona-${role.name}`}>Edit</button>
                )}
                {isEditing && (
                  <div className="persona-card__reorder-controls">
                    <button type="button" className="persona-gallery__move-btn" aria-label={`Move ${role.title} up`} disabled={isFirst} onClick={(e) => { e.stopPropagation(); moveRoleInCompleteList(role.name, -1); }} data-testid={`move-up-${role.name}`}>↑</button>
                    <button type="button" className="persona-gallery__move-btn" aria-label={`Move ${role.title} down`} disabled={isLast} onClick={(e) => { e.stopPropagation(); moveRoleInCompleteList(role.name, 1); }} data-testid={`move-down-${role.name}`}>↓</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default PersonaGallery;
