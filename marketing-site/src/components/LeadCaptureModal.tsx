import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, FileCheck2, ShieldCheck, X } from "lucide-react";
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
  const [step, setStep] = useState<1 | 2>(1);
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [website, setWebsite] = useState("");
  const [consent, setConsent] = useState(true);

  const [projectSlug, setProjectSlug] = useState(defaultProjectSlug);
  const [plotNumber, setPlotNumber] = useState(defaultPlotNumber);
  const [persona, setPersona] = useState<string>(BUYER_PERSONAS[0]);
  const [loanRequired, setLoanRequired] = useState(defaultLoanRequired);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setStep(1);
      setProjectSlug(defaultProjectSlug);
      setPlotNumber(defaultPlotNumber);
      setLoanRequired(defaultLoanRequired);
      setError("");
    }
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

  function validateStep1(): boolean {
    const cleanName = fullName.trim();
    const digits = phone.replace(/\D/g, "");
    if (cleanName.length < 2) {
      setError("Please enter your full name (at least 2 characters).");
      return false;
    }
    if (digits.length < 10 || digits.length > 15) {
      setError("Please enter a valid 10-digit mobile or WhatsApp number.");
      return false;
    }
    if (!consent) {
      setError("Please confirm communication consent so our Nagpur advisor can send your dossier.");
      return false;
    }
    setError("");
    return true;
  }

  async function submitLead() {
    if (!validateStep1()) return;
    setBusy(true);
    setError("");

    const payload = {
      fullName: fullName.trim(),
      phone: phone.trim(),
      email: email.trim(),
      persona,
      interest,
      project: selectedProject.slug,
      projectName: selectedProject.name,
      plotNumber: plotNumber.trim(),
      estimatedBudgetInr: defaultBudgetInr,
      loanRequired,
      message: message.trim(),
      consent,
      website,
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
        fullName: payload.fullName,
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

  async function handleFormSubmit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    if (step === 1) {
      await submitLead();
    } else {
      await submitLead();
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="lead-modal-title"
      data-lenis-prevent
      data-lenis-prevent-wheel
      data-lenis-prevent-touch
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 sm:p-5"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        data-lenis-prevent
        data-lenis-prevent-wheel
        data-lenis-prevent-touch
        className="modal-scroll relative max-h-[92svh] w-full max-w-xl overflow-y-auto overscroll-contain rounded-3xl border border-black/15 bg-[#F8F7F2] p-5 text-[#121D17] shadow-2xl dark:border-[#D4AF5F]/30 dark:bg-[#0D1512] dark:text-[#F2EFE7] sm:p-8"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close enquiry dialog"
          className="absolute top-4 right-4 z-10 flex h-9 w-9 items-center justify-center rounded-xl border border-black/15 bg-[#F8F7F2]/90 text-[#38463E] hover:border-[#8C6D2E] dark:border-white/15 dark:bg-[#0D1512]/90 dark:text-[#D5DDD7] dark:hover:border-[#D4AF5F] sm:top-5 sm:right-5"
        >
          <X className="h-4 w-4" />
        </button>

        {/* 2-Step Progress Indicator */}
        <div className="flex items-center gap-2 pr-10 text-[11px] font-semibold tracking-wider uppercase">
          <span
            className={`inline-flex items-center gap-1.5 ${
              step === 1 ? "text-[#8C6D2E] dark:text-[#E5C77B]" : "text-[#2E6B42] dark:text-[#7FBB8C]"
            }`}
          >
            {step === 2 ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <span className="font-mono-tabular">01</span>
            )}
            <span>Quick Contact</span>
          </span>
          <span className="h-px w-6 bg-black/15 dark:bg-white/20" aria-hidden="true" />
          <span
            className={`inline-flex items-center gap-1.5 ${
              step === 2 ? "text-[#8C6D2E] dark:text-[#E5C77B]" : "text-[#7A8880] dark:text-[#75867B]"
            }`}
          >
            <span className="font-mono-tabular">02</span>
            <span>Plot &amp; Loan Preferences (Optional)</span>
          </span>
        </div>

        <h2
          id="lead-modal-title"
          className="font-display mt-2.5 pr-8 text-3xl font-normal tracking-tight sm:text-4xl"
        >
          {plotNumber
            ? `Verify Plot ${plotNumber} in Writing`
            : "Get Written Cost Sheet & Buyer’s Guide"}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-[#526158] dark:text-[#A3B2A7]">
          Enter your name &amp; WhatsApp number to receive dated plot availability and immediately
          unlock our complimentary{" "}
          <strong className="font-medium text-[#121D17] dark:text-[#E5C77B]">
            14-Point Maharashtra Plot Buyer’s Guide
          </strong>
          .
        </p>

        {/* Pre-attached Context & Pricing Disclaimer */}
        <div className="mt-4 rounded-xl border border-[#D4AF5F]/35 bg-[#D4AF5F]/10 px-4 py-2.5 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-[#5E4A1E] dark:text-[#E5C77B]">
              {selectedProject.name}
              {plotNumber ? ` · Plot ${plotNumber}` : ""}
            </span>
            {defaultBudgetInr ? (
              <span className="font-mono-tabular font-semibold text-[#121D17] dark:text-white">
                Est. {formatINRCompact(defaultBudgetInr)}*
              </span>
            ) : null}
          </div>
          {defaultBudgetInr ? (
            <p className="mt-1 text-[10px] leading-snug text-[#5B685F] dark:text-[#A3B2A7]">
              *Indicative calculation only. Final payable amount is governed strictly by the dated
              written cost sheet, statutory Maharashtra duties, and bank sanction terms.
            </p>
          ) : null}
        </div>

        <form onSubmit={handleFormSubmit} className="mt-5 space-y-4">
          {/* Honeypot */}
          <input
            name="website"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
            tabIndex={-1}
            autoComplete="off"
            aria-hidden="true"
            className="pointer-events-none absolute -left-[9999px] h-0 w-0 opacity-0"
          />

          {step === 1 ? (
            <>
              <label className="block text-xs font-medium">
                <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Full Name</span>
                <input
                  name="fullName"
                  type="text"
                  required
                  minLength={2}
                  maxLength={120}
                  autoComplete="name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Dr. Rajesh Deshmukh / Your Name"
                  className="mt-1.5 min-h-[44px] w-full rounded-lg border border-black/15 bg-white px-3.5 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                />
              </label>

              <label className="block text-xs font-medium">
                <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Mobile / WhatsApp Number</span>
                <input
                  name="phone"
                  type="tel"
                  required
                  maxLength={32}
                  autoComplete="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+91 98XXX XXXXX"
                  className="mt-1.5 min-h-[44px] w-full rounded-lg border border-black/15 bg-white px-3.5 py-2.5 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                />
              </label>

              <label className="flex cursor-pointer items-start gap-2.5 pt-1 text-xs text-[#58675E] dark:text-[#98A89D]">
                <input
                  name="consent"
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  required
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[#D4AF5F]"
                />
                <span>
                  I consent to Realtor Estates sharing written plot availability, layout brochures,
                  and the 14-Point Maharashtra Buyer’s Guide for this enquiry. Zero data resale.
                </span>
              </label>

              {error ? (
                <p
                  role="alert"
                  className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-300"
                >
                  {error}
                </p>
              ) : null}

              <div className="grid grid-cols-1 gap-2.5 pt-1 sm:grid-cols-2">
                <button
                  type="submit"
                  disabled={busy}
                  className="inline-flex min-h-[48px] items-center justify-center gap-2 rounded-lg bg-[#10281E] px-4 py-3 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#183b2c] disabled:opacity-60 dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                >
                  <FileCheck2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>{busy ? "Confirming…" : "Unlock Guide & Cost Sheet Now"}</span>
                </button>

                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (validateStep1()) setStep(2);
                  }}
                  className="inline-flex min-h-[48px] items-center justify-center gap-1.5 rounded-lg border border-black/15 bg-white px-4 py-3 text-xs font-semibold text-[#16261E] transition-colors hover:border-[#8C6D2E] dark:border-white/15 dark:bg-[#131E19] dark:text-[#ECE8DD] dark:hover:border-[#D4AF5F]"
                >
                  <span>Customize Plot &amp; Loan Details</span>
                  <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                </button>
              </div>

              <div className="flex items-center justify-center gap-2 pt-1 text-[11px] text-[#64736A] dark:text-[#8E9F93]">
                <ShieldCheck className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                <span>Private Nagpur advisory · Direct official sheet verification</span>
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs font-medium">
                  <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Estate Corridor</span>
                  <select
                    value={projectSlug}
                    onChange={(e) => setProjectSlug(e.target.value)}
                    className="mt-1.5 min-h-[42px] w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                  >
                    {PROJECTS.map((p) => (
                      <option key={p.slug} value={p.slug}>
                        {p.name}
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
                    placeholder="e.g. 12 or Corner East"
                    className="mt-1.5 min-h-[42px] w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                  />
                </label>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs font-medium">
                  <span className="text-[#4B5A51] dark:text-[#B5C1B8]">Buying Goal</span>
                  <select
                    value={persona}
                    onChange={(e) => setPersona(e.target.value)}
                    className="mt-1.5 min-h-[42px] w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                  >
                    {BUYER_PERSONAS.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs font-medium">
                  <span className="text-[#4B5A51] dark:text-[#B5C1B8]">
                    Email <span className="font-normal opacity-70">(optional)</span>
                  </span>
                  <input
                    name="email"
                    type="email"
                    maxLength={160}
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@domain.com"
                    className="mt-1.5 min-h-[42px] w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                  />
                </label>
              </div>

              <label className="block text-xs font-medium">
                <span className="text-[#4B5A51] dark:text-[#B5C1B8]">
                  Questions or Preferred Visit Day{" "}
                  <span className="font-normal opacity-70">(optional)</span>
                </span>
                <textarea
                  name="message"
                  rows={2}
                  maxLength={1000}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Share preferred visit day, Vastu facing preference, or bank loan query..."
                  className="mt-1.5 w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-[#121D17] dark:border-white/15 dark:bg-[#131E19] dark:text-[#F2EFE7]"
                />
              </label>

              <label className="flex cursor-pointer items-center gap-2.5 text-xs text-[#49584F] dark:text-[#B5C1B8]">
                <input
                  type="checkbox"
                  checked={loanRequired}
                  onChange={(e) => setLoanRequired(e.target.checked)}
                  className="h-4 w-4 accent-[#D4AF5F]"
                />
                <span>Include SBI / HDFC / ICICI plot-loan pre-qualification sheet</span>
              </label>

              {error ? (
                <p
                  role="alert"
                  className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-300"
                >
                  {error}
                </p>
              ) : null}

              <div className="flex items-center gap-2.5 pt-1">
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="inline-flex min-h-[48px] items-center justify-center gap-1.5 rounded-lg border border-black/15 bg-white px-4 py-3 text-xs font-medium text-[#16261E] hover:border-[#8C6D2E] dark:border-white/15 dark:bg-[#131E19] dark:text-[#ECE8DD]"
                >
                  <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Back</span>
                </button>
                <button
                  type="submit"
                  disabled={busy}
                  className="flex-1 min-h-[48px] rounded-lg bg-[#10281E] px-5 py-3 text-sm font-semibold text-[#F6F5F0] transition-colors hover:bg-[#183b2c] disabled:opacity-60 dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                >
                  {busy ? "Confirming & Syncing CRM…" : "Confirm Request & Unlock Buyer’s Guide"}
                </button>
              </div>
            </>
          )}
        </form>
      </div>
    </div>
  );
}
