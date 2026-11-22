import { useEffect, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { BUYER_PERSONAS, PROJECTS } from "../data/projects";
import { formatINRCompact } from "../lib/finance";

export type LeadSubmissionResult = {
  referenceCode: string;
  fullName: string;
  projectSlug: string;
  projectName: string;
  plotNumber: string;
  persona: string;
  estimatedBudgetInr: number | null;
  crmSyncStatus: string;
  followUpSequence: Array<{
    step: number;
    dayOffset: number;
    subject: string;
    channel: string;
    status: string;
  }>;
};

type Props = {
  open: boolean;
  defaultProjectSlug: string;
  defaultPlotNumber?: string;
  defaultBudgetInr?: number | null;
  defaultLoanRequired?: boolean;
  interest?: "site-visit" | "plot-enquiry" | "callback";
  onClose: () => void;
  onSuccess: (result: LeadSubmissionResult) => void;
};

export default function LeadCaptureModal({
  open,
  defaultProjectSlug,
  defaultPlotNumber = "",
  defaultBudgetInr = null,
  defaultLoanRequired = false,
  interest = "site-visit",
  onClose,
  onSuccess,
}: Props) {
  const [projectSlug, setProjectSlug] = useState(defaultProjectSlug);
  const [plotNumber, setPlotNumber] = useState(defaultPlotNumber);
  const [persona, setPersona] = useState<string>(BUYER_PERSONAS[0]);
  const [loanRequired, setLoanRequired] = useState(defaultLoanRequired);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setProjectSlug(defaultProjectSlug);
    setPlotNumber(defaultPlotNumber);
    setLoanRequired(defaultLoanRequired);
    setError("");
  }, [defaultProjectSlug, defaultPlotNumber, defaultLoanRequired, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const selectedProject = PROJECTS.find((p) => p.slug === projectSlug) ?? PROJECTS[0];

  async function handleSubmit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(ev.currentTarget);

    const payload = {
      fullName: String(form.get("fullName") || ""),
      phone: String(form.get("phone") || ""),
      email: String(form.get("email") || ""),
      persona,
      interest,
      project: selectedProject.slug,
      projectName: selectedProject.name,
      plotNumber: plotNumber.trim(),
      estimatedBudgetInr: defaultBudgetInr,
      loanRequired,
      message: String(form.get("message") || ""),
      consent: form.get("consent") === "on",
      website: String(form.get("website") || ""),
    };

    try {
      const response = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as {
        ok?: boolean;
        error?: string;
        referenceCode?: string;
        lead?: {
          crmSyncStatus: string;
          followUpSequence: LeadSubmissionResult["followUpSequence"];
        };
      };
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Please review your contact details and try again.");
      }
      onSuccess({
        referenceCode: data.referenceCode || "RE-NGP-CONFIRMED",
        fullName: payload.fullName.trim(),
        projectSlug: selectedProject.slug,
        projectName: selectedProject.name,
        plotNumber: payload.plotNumber,
        persona,
        estimatedBudgetInr: defaultBudgetInr,
        crmSyncStatus: data.lead?.crmSyncStatus || "Queued in Local CRM",
        followUpSequence: data.lead?.followUpSequence || [],
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit enquiry just now.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="lead-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative max-h-[92svh] w-full max-w-xl overflow-y-auto rounded-2xl border border-black/15 bg-[#F8F7F2] p-6 text-[#121D17] dark:border-[#D4AF5F]/30 dark:bg-[#0D1512] dark:text-[#F2EFE7] sm:p-8">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close enquiry dialog"
          className="absolute top-5 right-5 flex h-9 w-9 items-center justify-center rounded-lg border border-black/15 text-[#38463E] hover:border-[#8C6D2E] dark:border-white/15 dark:text-[#D5DDD7] dark:hover:border-[#D4AF5F]"
        >
          <X className="h-4 w-4" />
        </button>

        <p className="text-xs font-medium tracking-wider text-[#8C6D2E] dark:text-[#D4AF5F]">
          Private Nagpur Advisory · Written Verification First
        </p>
        <h2
          id="lead-modal-title"
          className="font-display mt-2 pr-8 text-3xl font-normal tracking-tight sm:text-4xl"
        >
          {plotNumber
            ? `Verify Plot ${plotNumber} in Writing`
            : "Schedule Your Private Estate Visit"}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-[#526158] dark:text-[#A3B2A7]">
          Share your contact details for a written plot availability dossier and immediately unlock
          our complimentary{" "}
          <strong className="font-medium text-[#121D17] dark:text-[#E5C77B]">
            14-Point Maharashtra Plot Buyer’s Guide &amp; Checklist
          </strong>{" "}
          upon submission.
        </p>

        {defaultBudgetInr ? (
          <div className="mt-4 rounded-lg border border-[#D4AF5F]/35 bg-[#D4AF5F]/10 px-4 py-2.5 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[#5E4A1E] dark:text-[#E5C77B]">
                Indicative Estimate Attached*
              </span>
              <span className="font-mono-tabular font-semibold text-[#121D17] dark:text-white">
                {formatINRCompact(defaultBudgetInr)}
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-snug text-[#5B685F] dark:text-[#A3B2A7]">
              *Indicative calculation only. Final payable amount is governed strictly by the dated
              written cost sheet, statutory Maharashtra duties, and bank sanction terms.
            </p>
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium">
              <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Estate Corridor</span>
              <select
                value={projectSlug}
                onChange={(e) => setProjectSlug(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
              >
                {PROJECTS.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name} ({p.corridor})
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-xs font-medium">
              <span className="text-[#4B5A51] dark:text-[#B5C1B8]">
                Preferred Plot No. <span className="font-normal opacity-70">(optional)</span>
              </span>
              <input
                type="text"
                value={plotNumber}
                onChange={(e) => setPlotNumber(e.target.value)}
                maxLength={24}
                placeholder="e.g. 84 or Corner East"
                className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
              />
            </label>
          </div>

          <label className="block text-xs font-medium">
            <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Which best describes your goal?</span>
            <select
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
            >
              {BUYER_PERSONAS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>

          <label className="block text-xs font-medium">
            <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Full Name</span>
            <input
              name="fullName"
              type="text"
              required
              minLength={2}
              maxLength={120}
              autoComplete="name"
              placeholder="Dr. Rajesh Deshmukh / Your Name"
              className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
            />
          </label>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium">
              <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Phone / WhatsApp</span>
              <input
                name="phone"
                type="tel"
                required
                maxLength={32}
                autoComplete="tel"
                placeholder="+91 98XXX XXXXX"
                className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
              />
            </label>
            <label className="block text-xs font-medium">
              <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Email for Written Dossier</span>
              <input
                name="email"
                type="email"
                maxLength={160}
                autoComplete="email"
                placeholder="you@domain.com"
                className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
              />
            </label>
          </div>

          <label className="block text-xs font-medium">
            <span className="text-[#4B5A51] dark:text-[#B5C1B8]">
              Questions for our Nagpur advisor <span className="font-normal opacity-70">(optional)</span>
            </span>
            <textarea
              name="message"
              rows={2}
              maxLength={1000}
              placeholder="Share preferred visit day, Vastu facing preference, or bank loan query..."
              className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
            />
          </label>

          {/* Honeypot */}
          <input
            name="website"
            tabIndex={-1}
            autoComplete="off"
            aria-hidden="true"
            className="pointer-events-none absolute -left-[9999px] h-0 w-0 opacity-0"
          />

          <div className="space-y-2.5 pt-1 text-xs">
            <label className="flex cursor-pointer items-center gap-2.5 text-[#49584F] dark:text-[#B5C1B8]">
              <input
                type="checkbox"
                checked={loanRequired}
                onChange={(e) => setLoanRequired(e.target.checked)}
                className="h-4 w-4 accent-[#D4AF5F]"
              />
              <span>Include bank plot-loan pre-qualification sheet in my follow-up email</span>
            </label>

            <label className="flex cursor-pointer items-start gap-2.5 text-[#58675E] dark:text-[#98A89D]">
              <input
                name="consent"
                type="checkbox"
                required
                defaultChecked
                className="mt-0.5 h-4 w-4 shrink-0 accent-[#D4AF5F]"
              />
              <span>
                I consent to Realtor Estates contacting me with written plot status, layout sheets,
                and automated follow-up updates for this enquiry.
              </span>
            </label>
          </div>

          {error ? (
            <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-500 dark:text-red-300">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-[#10281E] px-5 py-3.5 text-sm font-semibold text-[#F6F5F0] transition-colors hover:bg-[#183b2c] disabled:opacity-60 dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
          >
            {busy ? "Confirming & Syncing CRM…" : "Confirm Request & View Dossier"}
          </button>
        </form>
      </div>
    </div>
  );
}
