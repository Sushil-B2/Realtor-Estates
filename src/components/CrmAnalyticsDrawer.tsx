import { useEffect, useState } from "react";
import { Activity, Eye, Mail, RefreshCw, Users, X } from "lucide-react";
import { formatINRCompact } from "../lib/finance";

type LeadItem = {
  id: string;
  referenceCode: string;
  fullName: string;
  phoneMasked: string;
  emailMasked: string;
  persona: string;
  project: string;
  plotNumber: string;
  estimatedBudgetInr: number | null;
  crmSyncStatus: string;
  createdAt: string;
  followUpSequence: Array<{
    step: number;
    dayOffset: number;
    subject: string;
    channel: string;
    status: string;
  }>;
};

type AnalyticsSummary = {
  pageViews: number;
  plotInspections: number;
  calculatorRuns: number;
  ctaClicks: number;
  leadCount: number;
  leadMagnetDownloads: number;
  conversionRatePct: number;
  topPlots: Array<{ label: string; views: number }>;
  heatmapPoints: Array<{ page: string; xPct: number; yPct: number; kind: string }>;
};

type Props = {
  open: boolean;
  heatmapOverlayEnabled: boolean;
  onToggleHeatmapOverlay: () => void;
  onClose: () => void;
};

export default function CrmAnalyticsDrawer({
  open,
  heatmapOverlayEnabled,
  onToggleHeatmapOverlay,
  onClose,
}: Props) {
  const [tab, setTab] = useState<"crm" | "analytics">("crm");
  const [leads, setLeads] = useState<LeadItem[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const [leadsRes, analyticsRes] = await Promise.all([
        fetch("/api/leads"),
        fetch("/api/analytics/summary"),
      ]);
      if (leadsRes.ok) {
        const lData = (await leadsRes.json()) as { leads: LeadItem[] };
        setLeads(lData.leads || []);
      }
      if (analyticsRes.ok) {
        const aData = (await analyticsRes.json()) as AnalyticsSummary;
        setSummary(aData);
      }
    } catch {
      // ignore transient errors
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      void loadData();
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Advisor CRM and Session Heatmap Console"
      className="fixed inset-0 z-50 flex justify-end bg-black/70"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-full w-full max-w-2xl flex-col border-l border-[#D4AF5F]/25 bg-[#0C1411] text-[#F2EFE7]">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
          <div>
            <p className="text-xs font-medium tracking-wider text-[#D4AF5F]">
              Realtor Estates · Acquisition Operations
            </p>
            <h2 className="font-display text-2xl font-normal text-white">
              CRM Pipeline &amp; Real-Time Heatmap
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadData()}
              aria-label="Refresh telemetry"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-[#D7E0D9] hover:border-[#D4AF5F]"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close drawer"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-[#D7E0D9] hover:border-[#D4AF5F]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Segmented Control Tabs */}
        <div className="flex items-center justify-between gap-3 border-b border-white/10 px-6 py-3">
          <div className="flex gap-1 rounded-lg border border-white/10 bg-[#080f0c] p-1">
            <button
              type="button"
              onClick={() => setTab("crm")}
              className={`inline-flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors ${
                tab === "crm" ? "bg-[#D4AF5F] text-[#080C0A]" : "text-[#AEBBAE] hover:text-white"
              }`}
            >
              <Users className="h-3.5 w-3.5" />
              <span>CRM &amp; Email Sequences ({leads.length})</span>
            </button>
            <button
              type="button"
              onClick={() => setTab("analytics")}
              className={`inline-flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors ${
                tab === "analytics"
                  ? "bg-[#D4AF5F] text-[#080C0A]"
                  : "text-[#AEBBAE] hover:text-white"
              }`}
            >
              <Activity className="h-3.5 w-3.5" />
              <span>Engagement &amp; Heatmap</span>
            </button>
          </div>

          <button
            type="button"
            onClick={onToggleHeatmapOverlay}
            className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              heatmapOverlayEnabled
                ? "border-[#D4AF5F] bg-[#D4AF5F]/20 text-[#E5C77B]"
                : "border-white/15 bg-[#12201a] text-[#C6D2C9] hover:border-[#D4AF5F]/60"
            }`}
          >
            <Eye className="h-3.5 w-3.5" />
            <span>{heatmapOverlayEnabled ? "Hide Live Heatmap" : "Overlay Live Heatmap"}</span>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {tab === "crm" ? (
            leads.length === 0 ? (
              <div className="rounded-xl border border-white/10 bg-[#101915] p-6 text-center">
                <p className="font-display text-2xl text-white">No leads captured yet.</p>
                <p className="mt-2 text-xs text-[#98A89D]">
                  Submit a site-visit request or reserve any plot from the 360° Land Inspector to
                  verify database storage and automated 3-step CRM follow-up scheduling.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {leads.map((lead) => (
                  <article
                    key={lead.id}
                    className="rounded-xl border border-white/10 bg-[#101915] p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="font-mono-tabular text-[11px] text-[#D4AF5F]">
                          {lead.referenceCode} · {lead.crmSyncStatus}
                        </p>
                        <h3 className="mt-0.5 text-base font-semibold text-white">
                          {lead.fullName}
                        </h3>
                        <p className="text-xs text-[#9AA99E]">
                          {lead.persona} · {lead.project}
                          {lead.plotNumber ? ` · Plot ${lead.plotNumber}` : ""}
                        </p>
                      </div>
                      <div className="text-right font-mono-tabular text-xs text-[#C7D1C9]">
                        <p>{lead.phoneMasked}</p>
                        {lead.estimatedBudgetInr ? (
                          <p className="text-[#E5C77B]">
                            Est. {formatINRCompact(lead.estimatedBudgetInr)}
                          </p>
                        ) : null}
                      </div>
                    </div>

                    <div className="mt-3 border-t border-white/10 pt-3">
                      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-[#B8C4BB]">
                        <Mail className="h-3.5 w-3.5 text-[#D4AF5F]" />
                        <span>Automated Follow-Up Sequence</span>
                      </p>
                      <div className="space-y-1.5">
                        {lead.followUpSequence.map((step) => (
                          <div
                            key={step.step}
                            className="flex items-center justify-between gap-3 text-[11px]"
                          >
                            <span className="truncate text-[#C6D1C9]">
                              Day {step.dayOffset}: {step.subject}
                            </span>
                            <span
                              className={`shrink-0 font-mono-tabular ${
                                step.status === "Sent" ? "text-[#6FA07A]" : "text-[#D4AF5F]"
                              }`}
                            >
                              {step.status}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )
          ) : (
            <div className="space-y-6">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {[
                  { label: "Session Views", value: summary?.pageViews ?? 0 },
                  { label: "360° Plot Opens", value: summary?.plotInspections ?? 0 },
                  { label: "EMI Runs", value: summary?.calculatorRuns ?? 0 },
                  { label: "Checklist Downloads", value: summary?.leadMagnetDownloads ?? 0 },
                  { label: "Qualified Leads", value: summary?.leadCount ?? 0 },
                  { label: "Lead Rate", value: `${summary?.conversionRatePct ?? 0}%` },
                ].map((metric) => (
                  <div
                    key={metric.label}
                    className="rounded-xl border border-white/10 bg-[#101915] p-4"
                  >
                    <p className="text-[11px] text-[#98A89D]">{metric.label}</p>
                    <p className="font-mono-tabular mt-1 text-2xl font-semibold text-white">
                      {metric.value}
                    </p>
                  </div>
                ))}
              </div>

              <div className="rounded-xl border border-white/10 bg-[#101915] p-4">
                <h3 className="text-xs font-semibold text-[#D4AF5F]">
                  Session Interaction Heatmap Preview ({summary?.heatmapPoints.length ?? 0} points)
                </h3>
                <div className="relative mt-3 h-48 w-full overflow-hidden rounded-lg border border-white/10 bg-[#080e0b]">
                  {(summary?.heatmapPoints ?? []).map((pt, i) => (
                    <span
                      key={`${pt.xPct}-${pt.yPct}-${i}`}
                      style={{
                        left: `${pt.xPct}%`,
                        top: `${pt.yPct}%`,
                      }}
                      className="pointer-events-none absolute -ml-3 -mt-3 h-6 w-6 rounded-full bg-[#D4AF5F]/35 blur-[3px]"
                    />
                  ))}
                  {(!summary || summary.heatmapPoints.length === 0) ? (
                    <div className="flex h-full items-center justify-center text-xs text-[#7E8F84]">
                      Interact with plots and buttons to populate live session click density.
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="rounded-xl border border-white/10 bg-[#101915] p-4">
                <h3 className="text-xs font-semibold text-[#D4AF5F]">
                  Most-Inspected Parcels (360° Views)
                </h3>
                {summary && summary.topPlots.length > 0 ? (
                  <ul className="mt-3 space-y-2 text-xs">
                    {summary.topPlots.map((tp) => (
                      <li
                        key={tp.label}
                        className="flex items-center justify-between border-b border-white/5 pb-1.5"
                      >
                        <span className="text-[#DCE3DD]">{tp.label}</span>
                        <span className="font-mono-tabular text-[#D4AF5F]">{tp.views} opens</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-xs text-[#8D9D92]">
                    Open any individual plot in 360° to track parcel demand here.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
