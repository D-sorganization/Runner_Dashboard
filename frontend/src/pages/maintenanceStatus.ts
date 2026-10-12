/** Pure status mapping for the fleet maintenance readout (#1936). */
export interface MaintenanceState {
  active?: boolean;
  drained?: boolean;
  rebooting?: boolean;
}

export function maintenanceStatus(m?: MaintenanceState): {
  label: string;
  tone: "neutral" | "success" | "warning" | "danger";
} {
  if (!m) return { label: "Normal operation", tone: "neutral" };
  if (m.rebooting) return { label: "Rebooting host", tone: "danger" };
  if (m.active && m.drained) return { label: "Drained (maintenance hold)", tone: "warning" };
  if (m.active) return { label: "Draining...", tone: "warning" };
  return { label: "Normal operation", tone: "neutral" };
}
