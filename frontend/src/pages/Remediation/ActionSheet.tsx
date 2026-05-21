import { useCallback, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { BottomSheet } from "../../primitives/BottomSheet";
import { TouchButton } from "../../primitives/TouchButton";
import type { AgentProvider, ProviderAvailability } from "./mobileTypes";
import { DEFAULT_PROVIDER_ORDER, getProviderLabel } from "./mobileTypes";

interface ActionSheetProps {
  isOpen: boolean;
  onClose: () => void;
  itemTitle: string;
  itemHtmlUrl: string;
  recommendedProviderId: string;
  providers: Record<string, AgentProvider>;
  availability: Record<string, ProviderAvailability>;
  onDispatch: (providerId: string) => void;
  dispatching: boolean;
}

export function ActionSheet({
  isOpen,
  onClose,
  itemTitle,
  itemHtmlUrl,
  recommendedProviderId,
  providers,
  availability,
  onDispatch,
  dispatching,
}: ActionSheetProps) {
  const [showAgentPicker, setShowAgentPicker] = useState(false);

  const handleOpenDesktop = useCallback(() => {
    window.open(itemHtmlUrl, "_blank", "noopener,noreferrer");
    onClose();
  }, [itemHtmlUrl, onClose]);

  const sortedProviders = Object.entries(providers)
    .map(([id, p]) => ({
      id,
      label: p.label ?? id,
      available: availability[id]?.available ?? false,
    }))
    .sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1;
      const ai = DEFAULT_PROVIDER_ORDER.indexOf(a.id);
      const bi = DEFAULT_PROVIDER_ORDER.indexOf(b.id);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1;
      if (bi !== -1) return 1;
      return a.label.localeCompare(b.label);
    });

  return (
    <>
      <BottomSheet
        isOpen={isOpen && !showAgentPicker}
        onClose={onClose}
        title={itemTitle}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <TouchButton
            aria-label={`Dispatch ${getProviderLabel(providers, recommendedProviderId)} for ${itemTitle}`}
            disabled={
              dispatching || !availability[recommendedProviderId]?.available
            }
            onClick={() => onDispatch(recommendedProviderId)}
            variant="primary"
            style={{ width: "100%", minHeight: 48, fontSize: 15 }}
          >
            {dispatching
              ? "Dispatching..."
              : `Dispatch ${getProviderLabel(providers, recommendedProviderId)}`}
          </TouchButton>

          <TouchButton
            aria-label="Pick a different agent"
            disabled={dispatching}
            onClick={() => setShowAgentPicker(true)}
            variant="default"
            style={{ width: "100%", minHeight: 48 }}
          >
            Pick agent...
          </TouchButton>

          <TouchButton
            aria-label="Open on desktop in new tab"
            onClick={handleOpenDesktop}
            variant="default"
            style={{ width: "100%", minHeight: 48 }}
          >
            Open on desktop
          </TouchButton>
        </div>
      </BottomSheet>

      <BottomSheet
        isOpen={isOpen && showAgentPicker}
        onClose={() => setShowAgentPicker(false)}
        title="Pick agent"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {sortedProviders.map(({ id, label, available }) => (
            <TouchButton
              key={id}
              aria-label={`Dispatch ${label}`}
              disabled={dispatching || !available}
              onClick={() => {
                setShowAgentPicker(false);
                onDispatch(id);
              }}
              variant={id === recommendedProviderId ? "primary" : "default"}
              style={{
                width: "100%",
                minHeight: 44,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{label}</span>
              <Badge tone={available ? "success" : "danger"} size="sm">
                {available ? "Ready" : "Unavailable"}
              </Badge>
            </TouchButton>
          ))}
        </div>
      </BottomSheet>
    </>
  );
}
