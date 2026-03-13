import { useState } from "react";
import type { FindingOccurrence, TriageStatus } from "../api";
import { updateTriage } from "../api";
import { SeverityBadge } from "./SeverityBadge";

interface Props {
  finding: FindingOccurrence;
  onTriageUpdated?: (finding: FindingOccurrence) => void;
  label?: string;
}

const triageOptions: { value: TriageStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "false_positive", label: "False Positive" },
  { value: "resolved", label: "Resolved" },
];

export function FindingCard({ finding, onTriageUpdated, label }: Props) {
  const [triageStatus, setTriageStatus] = useState<TriageStatus>(
    finding.triage?.status ?? "open",
  );
  const [triageNote, setTriageNote] = useState(finding.triage?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);

  async function handleTriage(status: TriageStatus) {
    setSaving(true);
    try {
      const triage = await updateTriage(finding.id, status, triageNote || null);
      setTriageStatus(triage.status);
      onTriageUpdated?.({ ...finding, triage });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={`finding-card severity-${finding.severity}`}>
      <div className="finding-header" onClick={() => setExpanded(!expanded)}>
        <div className="finding-title">
          {label && <span className="finding-label">{label}</span>}
          <SeverityBadge severity={finding.severity} />
          <strong>{finding.vulnerability_type}</strong>
          <span className="finding-location">
            {finding.file_path}:{finding.line_number}
          </span>
          {finding.github_deeplink && (
            <a
              href={finding.github_deeplink}
              target="_blank"
              rel="noopener noreferrer"
              className="github-deeplink"
              onClick={(e) => e.stopPropagation()}
              title="View code context in GitHub"
            >
              ↗ GitHub
            </a>
          )}
        </div>
        <span className="expand-icon">{expanded ? "▾" : "▸"}</span>
      </div>

      {expanded && (
        <div className="finding-body">
          <p className="finding-desc">{finding.description}</p>
          <details>
            <summary>Explanation</summary>
            <p>{finding.explanation}</p>
          </details>
          {finding.code_snippet && (
            <pre className="code-snippet">{finding.code_snippet}</pre>
          )}
          <div className="triage-controls">
            <select
              value={triageStatus}
              disabled={saving}
              onChange={(e) => {
                const newStatus = e.target.value as TriageStatus;
                setTriageStatus(newStatus);
                handleTriage(newStatus);
              }}
            >
              {triageOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Triage note…"
              value={triageNote}
              onChange={(e) => setTriageNote(e.target.value)}
              onBlur={() => handleTriage(triageStatus)}
              disabled={saving}
            />
          </div>
        </div>
      )}
    </div>
  );
}
