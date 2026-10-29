/**
 * PersonaAvatar.tsx — Shared persona/role avatar renderer for the Staff Console (#3501).
 *
 * Unifies the avatar-image-vs-letter-fallback logic and role-to-hue tint
 * previously duplicated independently in RosterRow and MessageItem, so the
 * same role always maps to the same image (or the same auto-route glyph)
 * everywhere it appears: roster, mobile persona cards (which reuse
 * RosterRow), and chat message headers.
 */
import React, { useState } from "react";
import { getRoleHue } from "./rosterUtils";

export type PersonaAvatarVariant = "roster" | "thread";

const VARIANT_CLASSES: Record<PersonaAvatarVariant, { base: string; image: string }> = {
  roster: { base: "roster-avatar", image: "roster-avatar--image" },
  thread: { base: "thread-avatar", image: "thread-avatar--image" },
};

export interface PersonaAvatarProps {
  /** Role name used for both the deterministic hue tint and the auto-route check. */
  roleName: string;
  /** Fallback letter shown when there is no image (or it fails to load). */
  initial: string;
  /** Persona image URL served by the backend (#2680), or undefined/null when none is set. */
  avatarUrl?: string | null;
  /**
   * True for the "Ask Barb" auto-route persona: it never shows a photo, only
   * the ★ glyph, on every surface — a specific message authored by `barb`
   * must look the same as the roster's barb row, not fall back to a real
   * photo in one place and the star in another.
   */
  isAutoRoute?: boolean;
  variant: PersonaAvatarVariant;
}

export const PersonaAvatar: React.FC<PersonaAvatarProps> = ({
  roleName,
  initial,
  avatarUrl,
  isAutoRoute = false,
  variant,
}) => {
  const [imageFailed, setImageFailed] = useState(false);
  const classes = VARIANT_CLASSES[variant];
  const showImage = !isAutoRoute && !!avatarUrl && !imageFailed;
  const displayInitial = isAutoRoute ? "★" : initial;

  if (showImage) {
    return (
      <img
        src={avatarUrl ?? undefined}
        alt=""
        aria-hidden="true"
        className={`${classes.base} ${classes.image}`}
        onError={() => setImageFailed(true)}
      />
    );
  }

  const hue = getRoleHue(roleName);
  const style: React.CSSProperties = isAutoRoute
    ? {}
    : {
        backgroundColor: `hsl(${hue}, 42%, 30%)`,
        color: "var(--text-on-accent)",
        borderColor: `hsl(${hue}, 48%, 42%)`,
      };

  return (
    <div className={classes.base} style={style} aria-hidden="true">
      {displayInitial}
    </div>
  );
};
