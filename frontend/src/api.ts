export type AnyRecord = Record<string, any>;
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((x: any) => x.msg).join("；")
        : detail || "请求失败，请重试",
    );
  }
  return body;
}
export const post = (path: string, body: any = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
export const labels: Record<string, string> = {
  queued: "排队中",
  validating: "校验中",
  analyzing: "分析中",
  generating: "生成中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  waiting: "等待",
  running: "运行中",
  skipped: "跳过",
  usable: "可用",
  limited: "受限",
  unavailable: "不可用",
  conflicting: "来源冲突",
  unverified: "时间待核验",
  ok: "可用",
  error: "不可用",
};
export const format = (value: any, digits = 2) =>
  value === null || value === undefined
    ? "—"
    : Number(value).toLocaleString("zh-CN", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const money = (value: any) =>
  value == null
    ? "—"
    : value >= 1e8
      ? format(value / 1e8) + " 亿"
      : format(value / 1e4) + " 万";
export const localTime = (value: string) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未知";
export function navigate(path: string) {
  window.location.hash = path;
}
export function useParams() {
  return new URLSearchParams(window.location.hash.split("?")[1] || "");
}
