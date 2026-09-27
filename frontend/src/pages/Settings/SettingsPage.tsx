/**
 * SettingsPage — the consolidated Settings area (#1338, SC-G6 owner decisions).
 *
 * One page with anchored sections instead of one nav tab per setting. Each
 * section is addressable as `/settings#<id>`; the retired per-tab routes
 * redirect to those anchors (see `routing.ts`). Section content supplies its
 * own heading, so the wrapper only names the region and anchors it.
 */
import React, { useEffect } from "react";
import { ThemeSettings } from "../../components/ThemeSettings";
import { LocalAppsPage } from "../LocalApps";

export interface SettingsSection {
  id: string;
  title: string;
  render: () => React.ReactNode;
}

/** Sections in display order. Ids are the URL anchors. */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: "theme", title: "Theme", render: () => <ThemeSettings /> },
  { id: "local-tools", title: "Local Tools", render: () => <LocalAppsPage /> },
];

function scrollToHash(): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const target = window.location.hash.replace(/^#/, "");
  if (!target) return;
  document.getElementById(target)?.scrollIntoView({ behavior: "smooth" });
}

export function SettingsPage(): React.ReactElement {
  useEffect(() => {
    scrollToHash();
    window.addEventListener("hashchange", scrollToHash);
    return () => window.removeEventListener("hashchange", scrollToHash);
  }, []);

  return (
    <div className="settings-page">
      <nav
        aria-label="Settings sections"
        style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", marginBottom: "1rem" }}
      >
        {SETTINGS_SECTIONS.map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            style={{ color: "var(--text-link, #58a6ff)", fontSize: "0.875rem" }}
          >
            {section.title}
          </a>
        ))}
      </nav>
      {SETTINGS_SECTIONS.map((section) => (
        <section
          key={section.id}
          id={section.id}
          aria-label={section.title}
          style={{ marginBottom: "1.5rem" }}
        >
          {section.render()}
        </section>
      ))}
    </div>
  );
}

export default SettingsPage;
