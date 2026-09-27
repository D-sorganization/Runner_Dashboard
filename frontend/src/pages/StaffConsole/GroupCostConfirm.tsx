/**
 * GroupCostConfirm.tsx — ask before a Board message or expert panel spends over the threshold (SC-D7 #1342, #1635).
 *
 * Renders the backend's estimate held by `useGroupCostGuard` or passed directly from `NewPanelForm`.
 */
import { TouchButton } from "../../primitives/TouchButton";
import type { GroupCostGuard } from "./useGroupCostGuard";
import "./groupTurn.css";

const usd = (value: number) => `$${value.toFixed(2)}`;

export interface GroupCostEstimateLike {
  total_cost_usd: number;
  threshold_usd: number;
  cost_per_seat: Record<string, number>;
}

export interface GroupCostConfirmProps {
  guard?: Pick<GroupCostGuard, "pending" | "confirm" | "cancel">;
  estimate?: GroupCostEstimateLike | null;
  message?: string | null;
  label?: string;
  titlePrefix?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm?: () => void | Promise<void>;
  onCancel?: () => void;
}

export function GroupCostConfirm(props: GroupCostConfirmProps) {
  const { guard } = props;

  const estimate = guard ? guard.pending : (props.estimate ?? null);
  const message = guard ? null : (props.message ?? null);

  if (!estimate && !message) return null;

  const ariaLabel = props.label || (guard ? "Board cost confirmation" : "Cost confirmation");
  const prefix = props.titlePrefix || (guard ? "Asking every Board seat is estimated at" : "Estimated cost is");
  const confirmText = props.confirmLabel || (guard ? "Send anyway" : "Confirm");
  const cancelText = props.cancelLabel || "Cancel";

  const handleConfirm = () => {
    if (guard) {
      void guard.confirm();
    } else {
      void props.onConfirm?.();
    }
  };

  const handleCancel = () => {
    if (guard) {
      guard.cancel();
    } else {
      props.onCancel?.();
    }
  };

  return (
    <aside className="group-cost-confirm" role="alert" aria-label={ariaLabel}>
      {estimate ? (
        <>
          <p>
            {prefix} <strong>{usd(estimate.total_cost_usd)}</strong>, over the{" "}
            {usd(estimate.threshold_usd)} threshold.
          </p>
          {estimate.cost_per_seat && Object.keys(estimate.cost_per_seat).length > 0 && (
            <ul>
              {Object.entries(estimate.cost_per_seat).map(([seat, cost]) => (
                <li key={seat}>
                  {seat}: {usd(cost)}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p>{message}</p>
      )}
      <div className="group-cost-confirm__actions">
        <TouchButton variant="primary" onClick={handleConfirm}>
          {confirmText}
        </TouchButton>
        <TouchButton onClick={handleCancel}>{cancelText}</TouchButton>
      </div>
    </aside>
  );
}
