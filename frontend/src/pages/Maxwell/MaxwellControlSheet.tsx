import { BottomSheet } from "../../primitives/BottomSheet";
import { TouchButton } from "../../primitives/TouchButton";
import type { ControlAction } from "./mobileTypes";

interface MaxwellControlSheetProps {
  isOpen: boolean;
  onClose: () => void;
  controlling: boolean;
  isRunning: boolean;
  onControl: (action: ControlAction) => void;
}

export function MaxwellControlSheet({
  isOpen,
  onClose,
  controlling,
  isRunning,
  onControl,
}: MaxwellControlSheetProps) {
  return (
    <BottomSheet isOpen={isOpen} onClose={onClose} title="Daemon Controls">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <TouchButton
          aria-label="Start Maxwell daemon"
          data-testid="maxwell-ctrl-start"
          disabled={controlling || isRunning}
          onClick={() => onControl("start")}
          variant="primary"
          style={{ minHeight: 48, width: "100%" }}
        >
          {controlling ? "Working…" : "Start Maxwell"}
        </TouchButton>
        <TouchButton
          aria-label="Stop Maxwell daemon"
          data-testid="maxwell-ctrl-stop"
          disabled={controlling || !isRunning}
          onClick={() => onControl("stop")}
          variant="danger"
          style={{ minHeight: 48, width: "100%" }}
        >
          {controlling ? "Working…" : "Stop Maxwell"}
        </TouchButton>
        <TouchButton
          aria-label="Restart Maxwell daemon"
          data-testid="maxwell-ctrl-restart"
          disabled={controlling}
          onClick={() => onControl("restart")}
          variant="default"
          style={{ minHeight: 48, width: "100%" }}
        >
          {controlling ? "Working…" : "Restart Maxwell"}
        </TouchButton>
      </div>
    </BottomSheet>
  );
}
