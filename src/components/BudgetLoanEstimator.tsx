import { useMemo, useState } from "react";
import { ArrowRight, Calculator, Compass } from "lucide-react";
import { PROJECTS } from "../data/projects";
import { calculatePlotFinance, formatINR, formatINRCompact } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import { LoanPartnerBanks } from "./ComplianceAndBankLogos";
import { PricingDisclaimer } from "./PricingDisclaimer";

type Props = {
  onOpenProject: (slug: string) => void;
  onRequestConsultation: (projectSlug: string, budgetInr: number) => void;
};

export default function BudgetLoanEstimator({ onOpenProject, onRequestConsultation }: Props) {
  const [selectedSlug, setSelectedSlug] = useState(PROJECTS[0].slug);
  const [plotUse, setPlotUse] = useState<"residential" | "commercial">("residential");
  const [areaSqFt, setAreaSqFt] = useState(1800);
  const [downPaymentPct, setDownPaymentPct] = useState(25);
  const [interestRate, setInterestRate] = useState(8.85);
  const [tenureYears, setTenureYears] = useState(15);
  const [cornerOrPark, setCornerOrPark] = useState(false);
  const [womenConcession, setWomenConcession] = useState(false);

  const project = PROJECTS.find((p) => p.slug === selectedSlug) ?? PROJECTS[0];
  const activeRatePerSqFt =
    plotUse === "commercial" && project.commercialRatePerSqFt
      ? project.commercialRatePerSqFt
      : project.baseRatePerSqFt;

  const result = useMemo(
    () =>
      calculatePlotFinance({
        areaSqFt,
        ratePerSqFt: activeRatePerSqFt,
        plcPercent: cornerOrPark ? 5 : 0,
        downPaymentPercent: downPaymentPct,
        annualInterestRate: interestRate,
        tenureYears,
        womenBuyerConcession: womenConcession,
      }),
    [areaSqFt, activeRatePerSqFt, cornerOrPark, downPaymentPct, interestRate, tenureYears, womenConcession],
  );

  const principalSharePct =
    result.totalLoanPayable > 0
      ? Math.round((result.loanPrincipal / result.totalLoanPayable) * 100)
      : 100;

  return (
    <section
      id="estimator"
      aria-labelledby="estimator-title"
      className="border-t border-black/10 bg-[#ECEAE1] py-16 sm:py-20 dark:border-white/10 dark:bg-[#0B120F] lg:py-28"
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
        <div className="grid grid-cols-1 items-end gap-6 lg:grid-cols-12 lg:gap-8">
          <div className="lg:col-span-7">
            <p className="inline-flex items-center gap-1.5 text-xs font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
              <Calculator className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Nagpur Plot Cost, Stamp Duty &amp; Loan EMI Calculator</span>
            </p>
            <h2
              id="estimator-title"
              className="font-display mt-3 text-balance text-3xl font-normal tracking-tight text-[#102018] sm:text-4xl lg:text-5xl dark:text-[#F4F1E7]"
            >
              Estimate your Nagpur plot budget, Maharashtra stamp duty &amp; monthly EMI.
            </h2>
          </div>
          <p className="text-sm leading-relaxed text-[#516057] dark:text-[#9AA99E] lg:col-span-5">
            Compare Residential and Commercial plot rates across Salai Dabha, Kanholi, and Hingna
            with SBI, HDFC, and ICICI indicative loan EMI figures in seconds.
          </p>
        </div>

        {/* Corridor selector tabs (interactive segmented buttons) */}
        <div
          role="group"
          aria-label="Choose Nagpur corridor"
          className="mt-10 flex flex-wrap gap-2"
        >
          {PROJECTS.map((item) => (
            <button
              key={item.slug}
              type="button"
              onClick={() => {
                setSelectedSlug(item.slug);
                trackEvent({ type: "calculator_update", page: "/", project: item.slug });
              }}
              className={`whitespace-nowrap rounded-lg border px-4 py-2.5 text-xs font-medium transition-colors ${
                selectedSlug === item.slug
                  ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                  : "border-black/15 bg-white text-[#3F4E45] hover:border-[#8C6D2E] dark:border-white/15 dark:bg-[#111A16] dark:text-[#B8C4BB] dark:hover:border-[#D4AF5F]"
              }`}
            >
              {item.name} · {item.corridor}
            </button>
          ))}
        </div>

        <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-12">
          {/* Controls Column */}
          <div className="space-y-6 rounded-2xl border border-black/10 bg-[#F7F6F1] p-6 dark:border-white/10 dark:bg-[#101814] sm:p-8 lg:col-span-7">
            {project.commercialRatePerSqFt ? (
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/10 pb-4 dark:border-white/10">
                <span className="text-xs font-medium text-[#46554C] dark:text-[#A9B8AD]">
                  Plot Category Rate ({project.name})
                </span>
                <div
                  role="group"
                  aria-label="Choose residential or commercial rate"
                  className="inline-flex rounded-lg border border-black/15 bg-white p-1 text-xs dark:border-white/15 dark:bg-[#0B120F]"
                >
                  <button
                    type="button"
                    onClick={() => setPlotUse("residential")}
                    className={`whitespace-nowrap rounded-md px-3 py-1.5 font-medium transition-colors ${
                      plotUse === "residential"
                        ? "bg-[#10281E] text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                        : "text-[#4F5E55] dark:text-[#A8B7AC]"
                    }`}
                  >
                    Residential · {formatINR(project.baseRatePerSqFt)}/sq.ft
                  </button>
                  <button
                    type="button"
                    onClick={() => setPlotUse("commercial")}
                    className={`whitespace-nowrap rounded-md px-3 py-1.5 font-medium transition-colors ${
                      plotUse === "commercial"
                        ? "bg-[#10281E] text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                        : "text-[#4F5E55] dark:text-[#A8B7AC]"
                    }`}
                  >
                    Commercial · {formatINR(project.commercialRatePerSqFt)}/sq.ft
                  </button>
                </div>
              </div>
            ) : null}

            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="est-area" className="text-sm font-medium text-[#18261F] dark:text-[#EFECE2]">
                  Plot Area (sq.ft)
                </label>
                <span className="font-mono-tabular text-base font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
                  {areaSqFt.toLocaleString("en-IN")} sq.ft ({Math.round(areaSqFt / 10.7639)} sq.m)
                </span>
              </div>
              <input
                id="est-area"
                type="range"
                min={1000}
                max={4800}
                step={100}
                value={areaSqFt}
                onChange={(e) => setAreaSqFt(Number(e.target.value))}
                className="estate-slider mt-3 w-full"
              />
              <div className="mt-1.5 flex justify-between font-mono-tabular text-[11px] text-[#6A7970] dark:text-[#84958A]">
                <span>1,000 sq.ft</span>
                <span>
                  Active rate: {formatINR(activeRatePerSqFt)}/sq.ft (
                  {plotUse === "commercial" && project.commercialRatePerSqFt
                    ? "Commercial"
                    : "Residential"}
                  )
                </span>
                <span>4,800 sq.ft</span>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              <div>
                <div className="flex items-baseline justify-between">
                  <label htmlFor="est-dp" className="text-sm font-medium text-[#18261F] dark:text-[#EFECE2]">
                    Down Payment
                  </label>
                  <span className="font-mono-tabular text-sm font-semibold text-[#18261F] dark:text-white">
                    {downPaymentPct}% ({formatINRCompact(result.downPaymentAmount)})
                  </span>
                </div>
                <input
                  id="est-dp"
                  type="range"
                  min={15}
                  max={80}
                  step={5}
                  value={downPaymentPct}
                  onChange={(e) => setDownPaymentPct(Number(e.target.value))}
                  className="estate-slider mt-3 w-full"
                />
              </div>

              <div>
                <div className="flex items-baseline justify-between">
                  <label htmlFor="est-tenure" className="text-sm font-medium text-[#18261F] dark:text-[#EFECE2]">
                    Loan Tenure
                  </label>
                  <span className="font-mono-tabular text-sm font-semibold text-[#18261F] dark:text-white">
                    {tenureYears} Years
                  </span>
                </div>
                <input
                  id="est-tenure"
                  type="range"
                  min={5}
                  max={25}
                  step={1}
                  value={tenureYears}
                  onChange={(e) => setTenureYears(Number(e.target.value))}
                  className="estate-slider mt-3 w-full"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 border-t border-black/10 pt-5 dark:border-white/10 sm:grid-cols-2">
              <div>
                <div className="flex items-baseline justify-between">
                  <label htmlFor="est-roi" className="text-sm font-medium text-[#18261F] dark:text-[#EFECE2]">
                    Bank Interest Rate (p.a.)
                  </label>
                  <span className="font-mono-tabular text-sm font-semibold text-[#18261F] dark:text-white">
                    {interestRate.toFixed(2)}%
                  </span>
                </div>
                <input
                  id="est-roi"
                  type="range"
                  min={7.5}
                  max={12.5}
                  step={0.15}
                  value={interestRate}
                  onChange={(e) => setInterestRate(Number(e.target.value))}
                  className="estate-slider mt-3 w-full"
                />
              </div>

              <div className="flex flex-col justify-center gap-2.5 text-xs">
                <label className="flex cursor-pointer items-center gap-2.5 text-[#3F4E45] dark:text-[#B9C5BC]">
                  <input
                    type="checkbox"
                    checked={cornerOrPark}
                    onChange={(e) => setCornerOrPark(e.target.checked)}
                    className="h-4 w-4 accent-[#D4AF5F]"
                  />
                  <span>Corner / Park-Facing Preferential Plot (+5% PLC)</span>
                </label>
                <label className="flex cursor-pointer items-center gap-2.5 text-[#3F4E45] dark:text-[#B9C5BC]">
                  <input
                    type="checkbox"
                    checked={womenConcession}
                    onChange={(e) => setWomenConcession(e.target.checked)}
                    className="h-4 w-4 accent-[#D4AF5F]"
                  />
                  <span>Maharashtra Women Buyer Concession (5% Stamp Duty)</span>
                </label>
              </div>
            </div>
          </div>

          {/* Live Breakdown Output Column */}
          <div className="flex flex-col justify-between rounded-2xl border border-[#D4AF5F]/30 bg-[#10241B] p-6 text-[#F4F1E7] dark:bg-[#111D17] sm:p-8 lg:col-span-5">
            <div>
              <p className="text-xs font-medium tracking-wider text-[#D4AF5F]">
                Estimated Monthly EMI
              </p>
              <p className="font-mono-tabular mt-1.5 text-4xl font-semibold text-[#F7F4EC]">
                {formatINR(result.monthlyEmi)}
                <span className="ml-1.5 text-sm font-normal text-[#A8B9AE]">/ month</span>
              </p>

              {/* Principal vs Interest Proportion Bar */}
              <div className="mt-5">
                <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full bg-[#D4AF5F] transition-all duration-300"
                    style={{ width: `${principalSharePct}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between font-mono-tabular text-[11px] text-[#AEBBAE]">
                  <span>Principal {principalSharePct}%</span>
                  <span>Interest {100 - principalSharePct}%</span>
                </div>
              </div>

              <dl className="mt-6 space-y-2.5 border-t border-white/10 pt-5 text-xs">
                <div className="flex justify-between">
                  <dt className="text-[#A8B8AD]">Base Land Cost ({project.name})</dt>
                  <dd className="font-mono-tabular font-medium text-white">{formatINR(result.baseCost)}</dd>
                </div>
                {result.plcAmount > 0 ? (
                  <div className="flex justify-between">
                    <dt className="text-[#A8B8AD]">Preferential Location (PLC)</dt>
                    <dd className="font-mono-tabular font-medium text-white">{formatINR(result.plcAmount)}</dd>
                  </div>
                ) : null}
                <div className="flex justify-between">
                  <dt className="text-[#A8B8AD]">
                    Maharashtra Stamp Duty ({result.stampDutyRate}%) + Registration
                  </dt>
                  <dd className="font-mono-tabular font-medium text-white">
                    {formatINR(result.stampDuty + result.registrationFee)}
                  </dd>
                </div>
                <div className="flex justify-between border-t border-white/10 pt-2.5 text-sm font-semibold">
                  <dt className="text-[#E5C77B]">Total All-Inclusive Budget</dt>
                  <dd className="font-mono-tabular text-[#E5C77B]">
                    {formatINRCompact(result.totalAcquisitionCost)}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="mt-6 flex flex-col gap-2.5">
              <button
                type="button"
                onClick={() => onRequestConsultation(project.slug, result.totalAcquisitionCost)}
                className="min-h-[46px] w-full rounded-lg bg-[#D4AF5F] px-4 py-3 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
              >
                Request Official Written Cost Sheet &amp; Loan Pre-Check
              </button>
              <button
                type="button"
                onClick={() => onOpenProject(project.slug)}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-lg border border-white/15 px-4 py-2.5 text-xs font-medium text-[#EFECE2] transition-colors hover:border-[#D4AF5F]"
              >
                <Compass className="h-3.5 w-3.5 text-[#D4AF5F]" aria-hidden="true" />
                <span>Open {project.name} 3D Map</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>

        {/* Mandatory Business & Customer Protection Pricing Disclaimer */}
        <PricingDisclaimer variant="default" className="mt-6" />

        {/* Partner Bank Logos for Nagpur NMRDA RL Plot Loans */}
        <LoanPartnerBanks variant="section" />
      </div>
    </section>
  );
}
