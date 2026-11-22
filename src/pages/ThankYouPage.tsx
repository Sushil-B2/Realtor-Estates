import { ArrowLeft, CheckCircle2, FileCheck2, Home, Mail } from "lucide-react";
import type { EstateProject } from "../data/projects";
import type { LeadSubmissionResult } from "../components/LeadCaptureModal";
import { formatINRCompact } from "../lib/finance";
import { trackLeadMagnet } from "../lib/analytics";
import { PricingDisclaimer } from "../components/PricingDisclaimer";

type Props = {
  project: EstateProject;
  confirmation: LeadSubmissionResult | null;
  onNavigate: (path: string) => void;
};

export default function ThankYouPage({ project, confirmation, onNavigate }: Props) {
  const defaultSequence = [
    {
      step: 1,
      dayOffset: 0,
      subject: `Your ${project.name} Dossier & Written Availability Sheet`,
      channel: "Email",
      status: "Sent",
    },
    {
      step: 2,
      dayOffset: 2,
      subject: `Nagpur Corridor Title & 7/12 Verification Checklist (${project.name})`,
      channel: "Email",
      status: "Scheduled",
    },
    {
      step: 3,
      dayOffset: 5,
      subject: "Private On-Site Boundary Walkthrough & Bank Loan Pre-Qualification",
      channel: "Concierge Call",
      status: "Scheduled",
    },
  ];

  const sequence =
    confirmation && confirmation.followUpSequence.length > 0
      ? confirmation.followUpSequence
      : defaultSequence;

  return (
    <section className="min-h-[82svh] bg-[#F6F5F0] py-16 dark:bg-[#080C0A] lg:py-24">
      <div className="mx-auto max-w-4xl px-6 lg:px-10">
        <div className="rounded-2xl border border-[#D4AF5F]/35 bg-white p-8 dark:bg-[#101814] sm:p-12">
          <div className="flex items-center gap-3 text-[#2D6A40] dark:text-[#7FBB8C]">
            <CheckCircle2 className="h-6 w-6 shrink-0" />
            <span className="font-mono-tabular text-xs font-semibold tracking-wider">
              CONFIRMED · {confirmation?.referenceCode || `RE-${project.slug.slice(0, 3).toUpperCase()}-NGP`}
            </span>
          </div>

          <h1 className="font-display mt-4 text-balance text-4xl font-normal tracking-tight text-[#102018] dark:text-[#F4F1E7] sm:text-5xl">
            Thank you{confirmation?.fullName ? `, ${confirmation.fullName}` : ""}. Your{" "}
            {project.name} request is registered.
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[#4F5E55] dark:text-[#A4B3A8]">
            Our Nagpur estate advisory desk has logged your inquiry in our CRM and initiated your
            written verification packet. No booking token is ever asked before you review the
            sanctioned sheet and inspect the ground in person.
          </p>

          {/* Summary Strip */}
          <dl className="mt-8 grid grid-cols-1 gap-4 border-y border-black/10 py-6 text-xs dark:border-white/10 sm:grid-cols-3">
            <div>
              <dt className="text-[#6B7970] dark:text-[#8E9F93]">Estate &amp; Corridor</dt>
              <dd className="mt-1 text-sm font-semibold text-[#102018] dark:text-white">
                {project.name} · {project.corridor}
              </dd>
            </div>
            <div>
              <dt className="text-[#6B7970] dark:text-[#8E9F93]">Parcel &amp; Goal</dt>
              <dd className="mt-1 text-sm font-semibold text-[#102018] dark:text-white">
                {confirmation?.plotNumber ? `Plot ${confirmation.plotNumber}` : "General Shortlist"}
                {confirmation?.persona ? ` · ${confirmation.persona}` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-[#6B7970] dark:text-[#8E9F93]">Est. Budget* / CRM Status</dt>
              <dd className="font-mono-tabular mt-1 text-sm font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
                {confirmation?.estimatedBudgetInr
                  ? `${formatINRCompact(confirmation.estimatedBudgetInr)}* · `
                  : ""}
                {confirmation?.crmSyncStatus || "Synced to CRM"}
              </dd>
            </div>
          </dl>
          {confirmation?.estimatedBudgetInr ? (
            <PricingDisclaimer variant="inline" className="mt-3" />
          ) : null}

          {/* Automated Follow-Up Sequence Roadmap */}
          <div className="mt-8">
            <h2 className="flex items-center gap-2 text-xs font-semibold tracking-wider text-[#8C6D2E] dark:text-[#D4AF5F]">
              <Mail className="h-4 w-4" />
              <span>Your Automated Concierge Follow-Up Sequence</span>
            </h2>
            <ol className="mt-4 space-y-3">
              {sequence.map((item) => (
                <li
                  key={item.step}
                  className="flex flex-col justify-between gap-2 rounded-xl border border-black/10 bg-[#F7F6F1] px-4 py-3.5 text-xs dark:border-white/10 dark:bg-[#0B120F] sm:flex-row sm:items-center"
                >
                  <div>
                    <span className="font-mono-tabular font-semibold text-[#8C6D2E] dark:text-[#D4AF5F]">
                      Day {item.dayOffset} · {item.channel}
                    </span>
                    <p className="mt-0.5 text-sm text-[#14231B] dark:text-[#EFECE2]">
                      {item.subject}
                    </p>
                  </div>
                  <span className="font-mono-tabular shrink-0 text-xs text-[#2D6A40] dark:text-[#7FBB8C]">
                    {item.status}
                  </span>
                </li>
              ))}
            </ol>
          </div>

          {/* Unlocked Lead Magnet: Buyer Guide shared exclusively after lead submission */}
          <div className="mt-9 rounded-xl border border-[#D4AF5F]/35 bg-[#F7F6F1] p-5 dark:bg-[#0B120F]">
            <p className="text-[11px] font-semibold tracking-wider text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
              Unlocked For You · Exclusive Lead Magnet
            </p>
            <p className="mt-1 text-sm text-[#18261F] dark:text-[#EFECE2]">
              Your complimentary <strong>14-Point Maharashtra Plot Buyer’s Guide &amp; Legal Checklist</strong>{" "}
              (covering 7/12 Satbara, NMRDA sanction, NA order, and MahaRERA checks) is ready below.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-4">
              <a
                href="/plot-buyer-checklist.html"
                target="_blank"
                rel="nofollow noopener noreferrer"
                onClick={() => trackLeadMagnet(project.slug, `/projects/${project.slug}/thank-you`)}
                className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg bg-[#10281E] px-6 py-3.5 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
              >
                <FileCheck2 className="h-4 w-4" />
                <span>Download Your Buyer’s Guide (14-Point Checklist)</span>
              </a>

              <button
                type="button"
                onClick={() => onNavigate(`/projects/${project.slug}`)}
                className="inline-flex min-h-[44px] items-center gap-2 whitespace-nowrap rounded-lg border border-black/15 px-5 py-3 text-xs font-medium text-[#18261F] hover:border-[#8C6D2E] dark:border-white/15 dark:text-[#EFECE2] dark:hover:border-[#D4AF5F]"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                <span>Return to {project.name} 3D Layout</span>
              </button>

              <button
                type="button"
                onClick={() => onNavigate("/")}
                className="inline-flex min-h-[44px] items-center gap-2 whitespace-nowrap rounded-lg border border-black/15 px-4 py-3 text-xs font-medium text-[#18261F] hover:border-[#8C6D2E] dark:border-white/15 dark:text-[#EFECE2] dark:hover:border-[#D4AF5F]"
              >
                <Home className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                <span>Home</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
