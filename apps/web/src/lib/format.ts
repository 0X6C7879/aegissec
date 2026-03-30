const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
});

const relativeDateFormatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });

export function formatDateTime(value: string): string {
  return dateTimeFormatter.format(new Date(value));
}

export function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  const deltaInMinutes = Math.round((timestamp - Date.now()) / 60_000);

  if (Math.abs(deltaInMinutes) < 60) {
    return relativeDateFormatter.format(deltaInMinutes, "minute");
  }

  const deltaInHours = Math.round(deltaInMinutes / 60);
  if (Math.abs(deltaInHours) < 48) {
    return relativeDateFormatter.format(deltaInHours, "hour");
  }

  const deltaInDays = Math.round(deltaInHours / 24);
  return relativeDateFormatter.format(deltaInDays, "day");
}

export function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }

  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }

  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatStatusLabel(status: string): string {
  switch (status) {
    case "idle":
      return "空闲";
    case "paused":
      return "已暂停";
    case "error":
      return "异常";
    case "done":
      return "完成";
    case "missing":
      return "未启动";
    case "stopped":
      return "已停止";
    case "running":
      return "运行中";
    case "connected":
      return "已连接";
    case "inactive":
      return "未启用";
    case "success":
      return "成功";
    case "failed":
      return "失败";
    case "timeout":
      return "超时";
    case "loaded":
      return "已加载";
    case "invalid":
      return "无效";
    case "ignored":
      return "已忽略";
    default:
      return status;
  }
}
