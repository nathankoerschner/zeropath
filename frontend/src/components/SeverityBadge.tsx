import type { Severity } from "../api";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`severity-badge ${severity}`}>
      {severity}
    </span>
  );
}
