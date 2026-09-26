/**
 * GroupCostConfirm.tsx — ask before a Board message spends over the threshold (SC-D7, #1342).
 *
 * Renders the backend's estimate held by `useGroupCostGuard`; nothing when no
 * send is waiting.
 */
import { TouchButton } from "../../primitives/TouchButton";
import type { GroupCostGuard } from "./useGroupCostGuard";
import "./groupTurn.css";

const usd = (value: number) => `$${value.toFixed(2)}`;

export function GroupCostConfirm({ guard }: { guard: Pick<GroupCostGuard, "pending" | "confirm" | "cancel"> }) {
  const estimate = guard.pending;
  if (!estimate) return null;
  return (
    <aside className="group-cost-confirm" role="alert" aria-label="Board cost confirmation">
      <p>
        Asking every Board seat is estimated at <strong>{usd(estimate.total_cost_usd)}</strong>, over the{" "}
        {usd(estimate.threshold_usd)} threshold.
      </p>
      <ul>
        {Object.entries(estimate.cost_per_seat).map(([seat, cost]) => (
          <li key={seat}>
            {seat}: {usd(cost)}
          </li>
        ))}
      </ul>
      <div className="group-cost-confirm__actions">
        <TouchButton variant="primary" onClick={() => void guard.confirm()}>
          Send anyway
        </TouchButton>
        <TouchButton onClick={guard.cancel}>Cancel</TouchButton>
      </div>
    </aside>
  );
}
