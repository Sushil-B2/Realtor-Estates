export type TrackEventPayload = {
  type: "pageview" | "plot_360_open" | "calculator_update" | "lead_magnet" | "cta_click" | "scroll_depth";
  page: string;
  project?: string;
  plotNumber?: string;
  meta?: string;
  xPct?: number;
  yPct?: number;
};

let consentGranted = true;

export function setAnalyticsConsent(allowed: boolean) {
  consentGranted = allowed;
}

export function trackEvent(payload: TrackEventPayload) {
  if (!consentGranted) return;
  try {
    void fetch("/api/analytics/event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
  } catch {
    // non-blocking telemetry
  }
}

export function trackLeadMagnet(project: string, page: string) {
  try {
    void fetch("/api/lead-magnet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, page }),
      keepalive: true,
    });
  } catch {
    // non-blocking
  }
}
