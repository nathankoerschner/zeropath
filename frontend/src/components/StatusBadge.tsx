import type { ScanStatus } from "../api";

export function StatusBadge({ status }: { status: ScanStatus }) {
  const label = status === "complete" ? "Completed" : status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <span className={`status-badge ${status}`}>
      <span className="status-dot" />
      {label}
    </span>
  );
}
