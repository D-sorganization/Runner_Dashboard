/**
 * PersonaEditor.tsx — Mobile personal display name and avatar editor (#3499).
 */
import React, { useRef, useState, useEffect } from "react";
import type { StaffRoleItem } from "./types";
import { PersonaAvatar } from "./PersonaAvatar";
import {
  savePresentationPrefs,
  validateDisplayName,
  validateAvatarFile,
  uploadCustomAvatar,
  resetCustomAvatar,
  MAX_DISPLAY_NAME_CHARS,
  type PresentationPrefsResponse,
} from "./personaGalleryUtils";

export interface PersonaEditorProps {
  roles: StaffRoleItem[];
  selectedRoleName?: string;
  onSelectRoleName?: (roleName: string) => void;
  prefs: PresentationPrefsResponse;
  onPrefsSaved: (prefs: PresentationPrefsResponse) => void;
  onClose?: () => void;
  className?: string;
}

export const PersonaEditor: React.FC<PersonaEditorProps> = ({
  roles,
  selectedRoleName,
  onSelectRoleName,
  prefs,
  onPrefsSaved,
  onClose,
  className = "",
}) => {
  const [activeRoleName, setActiveRoleName] = useState(selectedRoleName || roles[0]?.name || "");
  const activeRoleRef = useRef(activeRoleName);
  activeRoleRef.current = activeRoleName;
  const [displayName, setDisplayName] = useState(prefs.display_label || "");
  const [nameError, setNameError] = useState<string | null>(null);
  const [isSavingName, setIsSavingName] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const [isResettingAvatar, setIsResettingAvatar] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (selectedRoleName && selectedRoleName !== activeRoleName) setActiveRoleName(selectedRoleName);
  }, [selectedRoleName]);

  const currentRole = roles.find((r) => r.name === activeRoleName) || roles[0];
  const currentAvatarUrl = currentRole ? prefs.avatar_overrides[currentRole.name] || currentRole.avatar || null : null;

  const handleRoleChange = (newRole: string) => {
    setActiveRoleName(newRole);
    onSelectRoleName?.(newRole);
    setSelectedFile(null);
    setPreviewUrl(null);
    setAvatarError(null);
  };

  const handleSaveDisplayName = async () => {
    const trimmed = displayName.trim();
    const err = validateDisplayName(trimmed);
    if (err) { setNameError(err); return; }
    setIsSavingName(true);
    setNameError(null);
    try {
      const updated = await savePresentationPrefs({
        expected_revision: prefs.revision,
        display_label: trimmed,
        role_order: prefs.role_order,
        avatar_overrides: prefs.avatar_overrides,
      });
      setIsSavingName(false);
      onPrefsSaved(updated);
      setAnnouncement("Display name saved successfully.");
    } catch (e: unknown) {
      setIsSavingName(false);
      const m = e instanceof Error ? e.message : String(e);
      setNameError(m.includes("409") ? "Stale revision: preferences were updated elsewhere. Please refresh." : (m.includes("401") || m.includes("403") ? "Authentication expired or unauthorized. Please re-login." : m || "Failed to save."));
    }
  };

  const handleResetDisplayName = async () => {
    setIsSavingName(true);
    setNameError(null);
    try {
      const updated = await savePresentationPrefs({
        expected_revision: prefs.revision,
        display_label: "",
        role_order: prefs.role_order,
        avatar_overrides: prefs.avatar_overrides,
      });
      setIsSavingName(false);
      setDisplayName("");
      onPrefsSaved(updated);
      setAnnouncement("Display name reset to default.");
    } catch (e: unknown) {
      setIsSavingName(false);
      const m = e instanceof Error ? e.message : String(e);
      setNameError(m.includes("409") ? "Stale revision: preferences were updated elsewhere. Please refresh." : m || "Failed to reset.");
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const err = validateAvatarFile(file);
    if (err) { setAvatarError(err); setSelectedFile(null); setPreviewUrl(null); return; }
    setAvatarError(null);
    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
    setAnnouncement("Image selected. Ready to upload.");
  };

  const handleUploadAvatar = async () => {
    if (!selectedFile || !currentRole) return;
    const targetRole = currentRole.name;
    setIsUploadingAvatar(true);
    setAvatarError(null);
    try {
      const resp = await uploadCustomAvatar(targetRole, selectedFile, prefs.revision);
      setIsUploadingAvatar(false);
      if (activeRoleRef.current === targetRole) {
        setSelectedFile(null);
        setPreviewUrl(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
      onPrefsSaved({
        ...prefs,
        revision: prefs.revision + 1,
        avatar_overrides: { ...prefs.avatar_overrides, [targetRole]: resp.avatar_url },
      });
      setAnnouncement(`Avatar uploaded successfully for ${targetRole}.`);
    } catch (e: unknown) {
      setIsUploadingAvatar(false);
      const m = e instanceof Error ? e.message : String(e);
      setAvatarError(m.includes("409") ? "Stale revision: avatar was updated elsewhere. Please refresh." : (m.includes("401") || m.includes("403") ? "Authentication expired or unauthorized. Please re-login." : (m.includes("413") ? "Payload exceeds size limit." : m || "Failed to upload avatar.")));
    }
  };

  const handleResetAvatar = async () => {
    if (!currentRole) return;
    const targetRole = currentRole.name;
    setIsResettingAvatar(true);
    setAvatarError(null);
    try {
      await resetCustomAvatar(targetRole, prefs.revision);
      setIsResettingAvatar(false);
      if (activeRoleRef.current === targetRole) {
        setSelectedFile(null);
        setPreviewUrl(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
      const newOverrides = { ...prefs.avatar_overrides };
      delete newOverrides[targetRole];
      onPrefsSaved({ ...prefs, avatar_overrides: newOverrides });
      setAnnouncement(`Avatar reset to default for ${targetRole}.`);
    } catch (e: unknown) {
      setIsResettingAvatar(false);
      const m = e instanceof Error ? e.message : String(e);
      setAvatarError(m.includes("409") ? "Stale revision: avatar was updated elsewhere. Please refresh." : m || "Failed to reset avatar.");
    }
  };

  return (
    <div className={`persona-editor ${className}`} data-testid="persona-editor">
      <div className="sr-only" role="status" aria-live="polite" data-testid="persona-editor-status">{announcement}</div>
      <div className="persona-editor__header">
        <h2 className="persona-editor__title">Edit Presentation</h2>
        {onClose && <button type="button" className="persona-gallery__btn" onClick={onClose} aria-label="Close editor" data-testid="persona-editor-close-btn">Done</button>}
      </div>
      <div className="persona-editor__role-selector">
        <label htmlFor="persona-editor-role-select" className="persona-editor__section-title">Persona</label>
        <select id="persona-editor-role-select" className="persona-editor__select" value={activeRoleName} onChange={(e) => handleRoleChange(e.target.value)} aria-label="Select persona to edit" data-testid="persona-editor-role-select">
          {roles.map((r) => (<option key={r.name} value={r.name}>{r.title} ({r.name})</option>))}
        </select>
        {currentRole && <div className="persona-editor__canonical-info">Canonical role: <code data-testid="persona-editor-canonical-id">{currentRole.name}</code></div>}
      </div>
      <div className="persona-editor__section">
        <label htmlFor="persona-editor-name-input" className="persona-editor__section-title">Personal Display Name</label>
        <input id="persona-editor-name-input" type="text" className="persona-editor__input" maxLength={MAX_DISPLAY_NAME_CHARS} value={displayName} onChange={(e) => { setDisplayName(e.target.value); setNameError(validateDisplayName(e.target.value)); }} placeholder="Enter display name" aria-label="Personal display name" data-testid="persona-editor-name-input" />
        {nameError && <div className="persona-editor__error" role="alert" data-testid="persona-editor-name-error">{nameError}</div>}
        <div className="persona-editor__btn-row">
          <button type="button" className="persona-editor__btn persona-editor__btn--primary" onClick={handleSaveDisplayName} disabled={isSavingName || Boolean(nameError)} data-testid="persona-editor-save-name-btn">{isSavingName ? "Saving..." : "Save name"}</button>
          <button type="button" className="persona-editor__btn" onClick={() => { setDisplayName(prefs.display_label || ""); setNameError(null); setAnnouncement("Display name changes cancelled."); }} disabled={isSavingName} data-testid="persona-editor-cancel-name-btn">Cancel</button>
          <button type="button" className="persona-editor__btn persona-editor__btn--danger" onClick={handleResetDisplayName} disabled={isSavingName || !displayName} data-testid="persona-editor-reset-name-btn">Reset to default</button>
        </div>
      </div>
      <div className="persona-editor__section">
        <div className="persona-editor__section-title">Avatar Image</div>
        <div className="persona-editor__avatar-row">
          <div className="persona-editor__avatar-preview-box" data-testid="persona-editor-avatar-box">
            {currentRole && <PersonaAvatar roleName={currentRole.name} initial={currentRole.title ? currentRole.title.charAt(0) : "B"} avatarUrl={previewUrl || currentAvatarUrl} variant="roster" isAutoRoute={currentRole.name === "barb"} />}
          </div>
          <div className="persona-editor__btn-row">
            <input type="file" ref={fileInputRef} accept="image/png,image/webp" onChange={handleFileChange} aria-label="Choose avatar image" data-testid="persona-editor-file-input" style={{ display: "none" }} />
            <button type="button" className="persona-editor__btn" onClick={() => fileInputRef.current?.click()} disabled={isUploadingAvatar || isResettingAvatar} data-testid="persona-editor-choose-btn">Choose image</button>
            {previewUrl && (
              <>
                <button type="button" className="persona-editor__btn persona-editor__btn--primary" onClick={handleUploadAvatar} disabled={isUploadingAvatar} data-testid="persona-editor-upload-btn">{isUploadingAvatar ? "Uploading..." : "Upload image"}</button>
                <button type="button" className="persona-editor__btn" onClick={() => { setSelectedFile(null); setPreviewUrl(null); setAvatarError(null); if (fileInputRef.current) fileInputRef.current.value = ""; setAnnouncement("Avatar preview cancelled."); }} disabled={isUploadingAvatar} data-testid="persona-editor-cancel-preview-btn">Cancel preview</button>
              </>
            )}
            {Boolean(currentAvatarUrl) && !previewUrl && (
              <button type="button" className="persona-editor__btn persona-editor__btn--danger" onClick={handleResetAvatar} disabled={isResettingAvatar || isUploadingAvatar} data-testid="persona-editor-reset-avatar-btn">{isResettingAvatar ? "Resetting..." : "Reset avatar"}</button>
            )}
          </div>
        </div>
        {avatarError && <div className="persona-editor__error" role="alert" data-testid="persona-editor-avatar-error">{avatarError}</div>}
      </div>
    </div>
  );
};

export default PersonaEditor;
