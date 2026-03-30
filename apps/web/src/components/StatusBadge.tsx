import { formatStatusLabel } from "../lib/format";

type StatusBadgeProps = {
  status: string;
};

function getStatusClassName(status: string): string {
  if (status === "connected") {
    return "success";
  }

  if (status === "inactive") {
    return "paused";
  }

  if (status === "loaded") {
    return "success";
  }

  if (status === "invalid") {
    return "error";
  }

  if (status === "ignored") {
    return "paused";
  }

  return [
    "idle",
    "running",
    "paused",
    "error",
    "done",
    "missing",
    "stopped",
    "success",
    "failed",
    "timeout",
  ].includes(status)
    ? status
    : "idle";
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const className = getStatusClassName(status);

  return (
    <span className="status-pill">
      <span className={`status status-${className}`} aria-hidden="true" />
      <span className="status-label">{formatStatusLabel(status)}</span>
    </span>
  );
}
