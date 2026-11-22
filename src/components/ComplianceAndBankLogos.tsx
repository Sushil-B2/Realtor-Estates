import { ShieldCheck } from "lucide-react";
import type { EstateProject } from "../data/projects";

/**
 * Clean, minimal monochrome/gold-synced official compliance & approval emblems
 * (NMRDA, MahaRERA, NIT/Town Planning, UDCPR-2020 / Collector NA).
 */
export function GovtComplianceLogos({ compact = false }: { compact?: boolean }) {
  return (
    <div
      aria-label="Government planning and regulatory compliance references"
      className={
        compact
          ? "grid grid-cols-2 gap-3 sm:grid-cols-4"
          : "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
      }
    >
      {/* 1. NMRDA */}
      <div className="flex items-center gap-3.5 rounded-xl border border-black/10 bg-white/80 px-4 py-3 dark:border-[#D4AF5F]/20 dark:bg-[#0D1612]">
        <svg
          viewBox="0 0 48 48"
          className="h-10 w-10 shrink-0"
          aria-hidden="true"
        >
          <circle
            cx="24"
            cy="24"
            r="21"
            fill="none"
            className="stroke-[#8C6D2E] dark:stroke-[#D4AF5F]"
            strokeWidth="1.5"
          />
          <circle
            cx="24"
            cy="24"
            r="16.5"
            fill="none"
            className="stroke-[#8C6D2E]/45 dark:stroke-[#D4AF5F]/45"
            strokeWidth="0.9"
            strokeDasharray="2 1.5"
          />
          {/* Stylized Ashoka pillar / civic arch crest */}
          <path
            d="M24 11l3 4h-6l3-4zm-4.5 6h9v3h-9v-3zm1.5 4h6v7h-6v-7zm-4.5 8.5h15v2.5h-15v-2.5z"
            className="fill-[#10281E] dark:fill-[#E5C77B]"
          />
          <circle cx="24" cy="34.5" r="2" className="fill-[#8C6D2E] dark:fill-[#D4AF5F]" />
        </svg>
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-[#102018] dark:text-[#F4F1E7]">
            NMRDA
          </p>
          <p className="truncate text-[11px] text-[#55645B] dark:text-[#96A69B]">
            Nagpur Metro Region Dev. Authority
          </p>
        </div>
      </div>

      {/* 2. MahaRERA */}
      <div className="flex items-center gap-3.5 rounded-xl border border-black/10 bg-white/80 px-4 py-3 dark:border-[#D4AF5F]/20 dark:bg-[#0D1612]">
        <svg
          viewBox="0 0 48 48"
          className="h-10 w-10 shrink-0"
          aria-hidden="true"
        >
          <path
            d="M24 5L9 11v12c0 10.2 6.4 17.6 15 20.5 8.6-2.9 15-10.3 15-20.5V11L24 5z"
            fill="none"
            className="stroke-[#8C6D2E] dark:stroke-[#D4AF5F]"
            strokeWidth="1.6"
          />
          <path
            d="M15 29V18l4.5 5 4.5-6.5 4.5 6.5 4.5-5v11H15z"
            className="fill-[#10281E] dark:fill-[#E5C77B]"
          />
          <rect
            x="15"
            y="31"
            width="18"
            height="2.2"
            rx="1"
            className="fill-[#8C6D2E] dark:fill-[#D4AF5F]"
          />
        </svg>
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-[#102018] dark:text-[#F4F1E7]">
            MahaRERA
          </p>
          <p className="truncate text-[11px] text-[#55645B] dark:text-[#96A69B]">
            Maharashtra RERA Compliance Desk
          </p>
        </div>
      </div>

      {/* 3. NIT / NATP Sanctioned */}
      <div className="flex items-center gap-3.5 rounded-xl border border-black/10 bg-white/80 px-4 py-3 dark:border-[#D4AF5F]/20 dark:bg-[#0D1612]">
        <svg
          viewBox="0 0 48 48"
          className="h-10 w-10 shrink-0"
          aria-hidden="true"
        >
          <rect
            x="7"
            y="7"
            width="34"
            height="34"
            rx="8"
            fill="none"
            className="stroke-[#8C6D2E] dark:stroke-[#D4AF5F]"
            strokeWidth="1.5"
          />
          <path
            d="M14 33V20h5v13h-5zm7.5 0V14h5v19h-5zm7.5 0V23h5v10h-5z"
            className="fill-[#10281E] dark:fill-[#E5C77B]"
          />
          <path
            d="M12 34.5h24"
            className="stroke-[#8C6D2E] dark:stroke-[#D4AF5F]"
            strokeWidth="1.6"
          />
        </svg>
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-[#102018] dark:text-[#F4F1E7]">
            NIT / NATP
          </p>
          <p className="truncate text-[11px] text-[#55645B] dark:text-[#96A69B]">
            Town Planning Sanctioned Layouts
          </p>
        </div>
      </div>

      {/* 4. UDCPR-2020 & Collector NA */}
      <div className="flex items-center gap-3.5 rounded-xl border border-black/10 bg-white/80 px-4 py-3 dark:border-[#D4AF5F]/20 dark:bg-[#0D1612]">
        <svg
          viewBox="0 0 48 48"
          className="h-10 w-10 shrink-0"
          aria-hidden="true"
        >
          <polygon
            points="24,5 29,11 37,11 37,19 43,24 37,29 37,37 29,37 24,43 19,37 11,37 11,29 5,24 11,19 11,11 19,11"
            fill="none"
            className="stroke-[#8C6D2E] dark:stroke-[#D4AF5F]"
            strokeWidth="1.4"
          />
          <path
            d="M17.5 24.5l4.2 4.2 8.8-9.2"
            fill="none"
            className="stroke-[#10281E] dark:stroke-[#E5C77B]"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-[#102018] dark:text-[#F4F1E7]">
            UDCPR-2020 · RL / NA
          </p>
          <p className="truncate text-[11px] text-[#55645B] dark:text-[#96A69B]">
            Clear 7/12 Satbara &amp; NA Residential
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Clean, minimal Bank Loan Partner Strip for NMRDA-sanctioned Nagpur residential plots
 * (SBI Realty, HDFC Bank, ICICI Bank, Bank of Baroda, Axis Bank, LIC HFL).
 */
export function LoanPartnerBanks({ variant = "section" }: { variant?: "section" | "compact" }) {
  const banks = [
    {
      name: "SBI Realty",
      sub: "State Bank of India",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <circle cx="16" cy="16" r="12" fill="#2860A8" />
          <circle cx="16" cy="14" r="3.6" fill="#FFFFFF" />
          <rect x="14.7" y="16.5" width="2.6" height="11.5" fill="#FFFFFF" />
        </svg>
      ),
    },
    {
      name: "HDFC Bank",
      sub: "Plot & Composite Loan",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <rect x="4" y="4" width="24" height="24" rx="2" fill="#D32F2F" />
          <rect x="9" y="9" width="14" height="14" fill="#FFFFFF" />
          <rect x="13" y="13" width="6" height="6" fill="#0D47A1" />
          <rect x="14.5" y="4" width="3" height="24" fill="#FFFFFF" opacity="0.22" />
        </svg>
      ),
    },
    {
      name: "ICICI Bank",
      sub: "Land & Home Finance",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <rect x="4" y="4" width="24" height="24" rx="5" fill="#B02A30" />
          <path
            d="M11 22c0-5.2 2.4-10.5 6.8-12.5-1.4 3.4-1.6 7.4-.2 12.5H11z"
            fill="#F58220"
          />
          <circle cx="17.8" cy="10.2" r="2" fill="#FFFFFF" />
        </svg>
      ),
    },
    {
      name: "Bank of Baroda",
      sub: "Baroda Home / Plot",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <rect x="4" y="4" width="24" height="24" rx="5" fill="#F26522" />
          <path
            d="M11 10h6.8c2.4 0 4 1.3 4 3.2 0 1.4-.9 2.4-2.1 2.7 1.6.4 2.6 1.5 2.6 3.1 0 2.2-1.8 3.5-4.4 3.5H11V10zm3 4.8h3.2c1 0 1.6-.5 1.6-1.3s-.6-1.3-1.6-1.3H14v2.6zm0 5.3h3.6c1.1 0 1.8-.5 1.8-1.4s-.7-1.4-1.8-1.4H14v2.8z"
            fill="#FFFFFF"
          />
        </svg>
      ),
    },
    {
      name: "Axis Bank",
      sub: "Approved Layout Loans",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <rect x="4" y="4" width="24" height="24" rx="5" fill="#97144D" />
          <path d="M16 8.5L23.5 23h-4.2l-3.3-6.6L12.7 23H8.5L16 8.5z" fill="#FFFFFF" />
        </svg>
      ),
    },
    {
      name: "LIC HFL",
      sub: "LIC Housing Finance",
      mark: (
        <svg viewBox="0 0 32 32" className="h-6 w-6 shrink-0" aria-hidden="true">
          <rect x="4" y="4" width="24" height="24" rx="5" fill="#184285" />
          <path
            d="M16 9l7 6.2V23h-4.5v-5h-5v5H9v-7.8L16 9z"
            fill="#F4C430"
          />
        </svg>
      ),
    },
  ];

  if (variant === "compact") {
    return (
      <div className="rounded-xl border border-white/10 bg-[#0b120f] p-3">
        <p className="text-[10px] font-medium tracking-wider text-[#9BB0A1] uppercase">
          Eligible Plot Loan Financing (Up to 75% LTV · NMRDA RL Plots)
        </p>
        <div className="mt-2.5 grid grid-cols-3 gap-2">
          {banks.map((bank) => (
            <div
              key={bank.name}
              className="flex items-center gap-2 rounded-lg border border-white/8 bg-[#101915] px-2 py-1.5"
            >
              {bank.mark}
              <span className="truncate text-[11px] font-medium text-[#E8E4D8]">{bank.name}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mt-8 rounded-2xl border border-black/10 bg-[#F7F6F1] p-6 dark:border-white/10 dark:bg-[#101814]">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-medium tracking-[0.14em] text-[#8C6D2E] dark:text-[#D4AF5F]">
            Banking &amp; Housing Finance Ecosystem · Nagpur NMRDA Layouts
          </p>
          <p className="mt-1 text-sm text-[#4F5E55] dark:text-[#98A89D]">
            Major nationalized &amp; private banks offering up to 70%–75% LTV plot and composite
            construction loans on sanctioned NMRDA / RL residential layouts.
          </p>
        </div>
        <span className="font-mono-tabular shrink-0 text-[11px] text-[#6B7970] dark:text-[#84958A]">
          Subject to bank panel legal &amp; technical clearance
        </span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {banks.map((bank) => (
          <div
            key={bank.name}
            className="flex items-center gap-2.5 rounded-xl border border-black/8 bg-white px-3.5 py-3 dark:border-white/10 dark:bg-[#0B120F]"
          >
            {bank.mark}
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                {bank.name}
              </p>
              <p className="truncate text-[10px] text-[#5C6B61] dark:text-[#8E9F93]">{bank.sub}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Clean NMRDA Digital Sign & Regulatory Sanction Stamp Panel for each project landing page.
 * Displays ONLY public regulatory references from the layout PDFs — zero personal names/phones/addresses.
 */
export function ProjectSanctionStamp({ project }: { project: EstateProject }) {
  const { compliance } = project;
  return (
    <div className="rounded-2xl border border-[#D4AF5F]/35 bg-[#F7F6F1] p-6 dark:bg-[#0E1813]">
      <div className="flex flex-col justify-between gap-4 border-b border-black/10 pb-5 dark:border-white/10 md:flex-row md:items-center">
        <div className="flex items-start gap-3.5">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-[#D4AF5F]/40 bg-[#10241B] text-[#E5C77B]">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[11px] font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
              {compliance.authority} · Official Regulatory Reference
            </p>
            <h3 className="font-display mt-0.5 text-2xl text-[#102018] dark:text-[#F4F1E7]">
              {compliance.sanctionRef}
            </h3>
          </div>
        </div>

        {/* Digital Sign Stamp Seal */}
        <div className="flex items-center gap-3 self-start rounded-xl border border-dashed border-[#8C6D2E]/60 bg-white/70 px-4 py-2.5 dark:border-[#D4AF5F]/45 dark:bg-[#09110D] md:self-auto">
          <svg viewBox="0 0 36 36" className="h-8 w-8 shrink-0" aria-hidden="true">
            <circle
              cx="18"
              cy="18"
              r="15.5"
              fill="none"
              className="stroke-[#2D6A40] dark:stroke-[#7FBB8C]"
              strokeWidth="1.5"
              strokeDasharray="3 1.5"
            />
            <path
              d="M12.5 18.2l3.5 3.5 7.5-7.8"
              fill="none"
              className="stroke-[#2D6A40] dark:stroke-[#7FBB8C]"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <div className="text-[11px] leading-tight">
            <p className="font-semibold text-[#173524] dark:text-[#9FD3AB]">
              {compliance.digitalSignStamp}
            </p>
            <p className="mt-0.5 text-[#58685E] dark:text-[#8EA093]">
              Personal signatory details redacted for privacy
            </p>
          </div>
        </div>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-4 text-xs sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">Survey / Kh. No.</dt>
          <dd className="font-mono-tabular mt-1 font-semibold text-[#102018] dark:text-white">
            {project.khNo}
          </dd>
        </div>
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">P.H. No. &amp; Mouza</dt>
          <dd className="mt-1 font-semibold text-[#102018] dark:text-white">
            {compliance.phNo} · {project.mouza}
          </dd>
        </div>
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">D.P. / Regulation</dt>
          <dd className="font-mono-tabular mt-1 font-semibold text-[#102018] dark:text-white">
            {compliance.regulationRef}
          </dd>
        </div>
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">Pro-Rata FSI</dt>
          <dd className="font-mono-tabular mt-1 font-semibold text-[#102018] dark:text-white">
            {compliance.proRataFsi}
          </dd>
        </div>
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">Open / Amenity</dt>
          <dd className="font-mono-tabular mt-1 font-semibold text-[#102018] dark:text-white">
            {compliance.openAndAmenity}
          </dd>
        </div>
        <div>
          <dt className="text-[#66756C] dark:text-[#8A9B90]">MahaRERA Status</dt>
          <dd className="mt-1 font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
            {compliance.reraStatus}
          </dd>
        </div>
      </dl>
    </div>
  );
}
