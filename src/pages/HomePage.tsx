import { lazy, Suspense } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Box,
  Building2,
  CalendarCheck,
  Compass,
  FileCheck2,
  Home,
  Landmark,
  MapPin,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { PROJECTS, summarizeLayout } from "../data/projects";
import { formatINR } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import LayoutMap2D from "../components/LayoutMap2D";
import BudgetLoanEstimator from "../components/BudgetLoanEstimator";
import { GovtComplianceLogos } from "../components/ComplianceAndBankLogos";
import { PricingDisclaimer, WhatsAppIcon } from "../components/PricingDisclaimer";

const CinematicJourney3D = lazy(() => import("../components/three/CinematicJourney3D"));

type Props = {
  onNavigate: (path: string) => void;
  onOpenLeadModal: (opts: {
    projectSlug?: string;
    plotNumber?: string;
    budgetInr?: number | null;
    interest?: "site-visit" | "plot-enquiry" | "callback";
  }) => void;
};

const HOME_FAQS = [
  {
    q: "What are the best NMRDA & RERA approved residential and commercial plots in Nagpur right now?",
    a: "Realtor Estates offers NMRDA-sanctioned residential and commercial plots across Nagpur’s top growth corridors: Samruddhi Estate Phase 3 & Phase 4 in Salai Dabha (Residential from ₹1,700/sq.ft*, Commercial from ₹1,750/sq.ft*), Bhagirathi Nagar - 4 in Mouza Kanholi (Residential from ₹1,990/sq.ft*, Commercial from ₹2,050/sq.ft*), and Ananta & Akasha on the 30m Hingna Boulevard.",
  },
  {
    q: "How can I inspect Nagpur plot sizes, road width, and Vastu direction in 3D before visiting?",
    a: "Tap any plot on our 3D WebGL layout or 2D sheet to launch its 360-degree interactive land model. Check exact meter/feet dimensions, wide 9m–30m frontage roads, live true-north Vastu compass bearings, allotted neighbors, and estimated plot-loan EMI right from your phone.",
  },
  {
    q: "Are plot loans from SBI, HDFC, ICICI, and Bank of Baroda available on NMRDA RL plots in Nagpur?",
    a: "Yes. NMRDA-sanctioned RL plots with clear 7/12 (Satbara) extracts and NA orders qualify for leading bank plot loans and composite plot-plus-construction finance, subject to individual bank credit norms and legal valuation.",
  },
  {
    q: "How do I get the 14-Point Maharashtra Plot Buyer’s Guide & 7/12 Checklist?",
    a: "Submit a quick site-visit or WhatsApp enquiry on any project page to instantly unlock our complimentary 14-Point Maharashtra Plot Buyer’s Guide covering 7/12 Satbara verification, 30-year title search, NMRDA layout sanction, NA conversion, and MahaRERA checks.",
  },
  {
    q: "Can I share a shortlisted Nagpur plot and budget calculation with my family on WhatsApp?",
    a: "Yes. Inside every 360° plot inspector, tap 'Share Plot Availability & Price Brief' to copy the exact plot dimensions, compass facing, frontage road, indicative budget, and a direct 360° link for family or bank review.",
  },
];

export default function HomePage({ onNavigate, onOpenLeadModal }: Props) {
  const scrollToEstates = () => {
    document.getElementById("estates")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="overflow-x-clip">
      {/* Hero Section — High-Contrast Mobile-First Architectural Focal Point */}
      <section
        aria-labelledby="hero-headline"
        className="relative flex min-h-[calc(100svh-60px)] items-end overflow-hidden bg-[#060B09] text-[#F5F2E9] md:min-h-[calc(100svh-72px)]"
      >
        <img
          src="/src/assets/images/hero_nagpur_estate_1795287010697.jpg"
          alt="Twilight aerial view of NMRDA sanctioned residential and commercial plots in Nagpur, Maharashtra"
          width={1600}
          height={900}
          fetchPriority="high"
          decoding="async"
          referrerPolicy="no-referrer"
          className="absolute inset-0 h-full w-full object-cover object-center"
        />
        {/* Deepened Multi-Directional Readability Scrim for WCAG AAA Text Clarity on All Screens */}
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-r from-[#050A08]/96 via-[#060C09]/84 to-[#060C09]/45"
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-t from-[#050A08]/98 via-[#050A08]/45 to-[#050A08]/40"
        />

        <div className="relative z-10 mx-auto w-full max-w-7xl px-4 pt-16 pb-12 sm:px-6 sm:pt-20 sm:pb-16 lg:px-10 lg:pb-20">
          <div className="max-w-3xl">
            <p className="inline-flex items-center gap-1.5 text-[11px] font-semibold tracking-[0.16em] text-[#E5C77B] uppercase sm:text-xs">
              <MapPin className="h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
              <span>NMRDA &amp; RERA Plots in Nagpur · Salai Dabha · Kanholi · Hingna</span>
            </p>

            <h1
              id="hero-headline"
              className="font-display mt-3.5 text-balance text-[2.75rem] leading-[0.98] font-normal tracking-tight text-[#FFFFFF] sm:text-6xl lg:text-[4.75rem]"
            >
              Buy Your Estates,
              <br />
              <em className="text-[#E5C77B]">Build Your Legacy.</em>
            </h1>

            <p className="mt-3 text-xs font-semibold tracking-[0.14em] text-[#F0D78C] uppercase sm:text-sm">
              Living Legacy Lifestyle — Residential &amp; Commercial Plots in Nagpur from ₹1,700/sq.ft*
            </p>

            {/* Concise, High-Contrast Keyword-Rich Subheading */}
            <p className="mt-3.5 max-w-2xl text-base leading-relaxed font-normal text-[#EEF3EF] sm:text-lg">
              Inspect NMRDA-sanctioned Nagpur plots in interactive 3D &amp; 360° with verified road
              widths, Vastu compass bearings, and bank plot-loan EMI estimates — before your site
              visit.
            </p>

            {/* Actionable Icon-Driven CTAs (Touch-Friendly >= 48px) */}
            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <button
                type="button"
                onClick={() => {
                  trackEvent({ type: "cta_click", page: "/", meta: "hero_explore_estates" });
                  scrollToEstates();
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2.5 whitespace-nowrap rounded-lg bg-[#D4AF5F] px-6 py-3 text-sm font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b] sm:w-auto"
              >
                <Compass className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>Explore 3D Nagpur Layouts</span>
                <ArrowRight className="h-4 w-4 shrink-0" aria-hidden="true" />
              </button>

              <button
                type="button"
                onClick={() => {
                  trackEvent({ type: "cta_click", page: "/", meta: "hero_book_visit" });
                  onOpenLeadModal({ interest: "site-visit" });
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-white/30 bg-[#0b1511]/90 px-5 py-3 text-sm font-medium text-[#F5F2E9] transition-colors hover:border-[#D4AF5F] hover:text-[#E5C77B] sm:w-auto"
              >
                <CalendarCheck className="h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                <span>Book Free Site Visit</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  trackEvent({ type: "cta_click", page: "/", meta: "hero_whatsapp_dossier" });
                  onOpenLeadModal({ interest: "callback" });
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-[#25D366]/45 bg-[#0c2417]/90 px-5 py-3 text-sm font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366] hover:bg-[#10301e] sm:w-auto"
              >
                <WhatsAppIcon className="h-4 w-4 shrink-0 text-[#25D366]" />
                <span>WhatsApp Plot Dossier</span>
              </button>
            </div>
          </div>

          {/* Icon-Backed Real-Estate Trust Highlights */}
          <div className="mt-10 grid grid-cols-1 gap-4 border-t border-white/15 pt-6 text-xs text-[#DCE4DE] sm:mt-12 sm:grid-cols-3 sm:gap-6">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <Box className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <strong className="font-semibold text-white">360° 3D Plot Inspection</strong>
                <p className="mt-0.5 text-[#B4C2B8]">
                  Rotate every plot with exact m/ft edges, 9m–30m roads &amp; Vastu compass.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <strong className="font-semibold text-white">NMRDA Sanctioned &amp; RL Clear</strong>
                <p className="mt-0.5 text-[#B4C2B8]">
                  Verified 7/12 title layouts with marked Available, Reserved &amp; Sold status.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <Landmark className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <strong className="font-semibold text-white">Bank Loan &amp; EMI Planner</strong>
                <p className="mt-0.5 text-[#B4C2B8]">
                  SBI, HDFC &amp; ICICI eligible plots with Maharashtra stamp-duty calculator*.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Prominent Direct Project Redirect Bar — Smooth 1-Tap Navigation to Every Nagpur Estate */}
      <nav
        aria-label="Direct Nagpur estate links"
        className="border-b border-black/10 bg-[#ECEAE1] py-4 dark:border-[#D4AF5F]/15 dark:bg-[#0B120F]"
      >
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <span className="inline-flex items-center gap-2 text-[11px] font-semibold tracking-[0.14em] text-[#7E632B] uppercase dark:text-[#D4AF5F]">
              <Building2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>Quick Jump to Nagpur Layouts</span>
            </span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:flex lg:flex-wrap lg:items-center">
              {PROJECTS.map((project) => (
                <a
                  key={project.slug}
                  href={`/projects/${project.slug}`}
                  onClick={(e) => {
                    e.preventDefault();
                    trackEvent({ type: "cta_click", page: "/", project: project.slug, meta: "quick_jump_bar" });
                    onNavigate(`/projects/${project.slug}`);
                  }}
                  className="inline-flex min-h-[42px] items-center justify-between gap-2 rounded-lg border border-black/10 bg-white px-3.5 py-2 text-xs font-medium text-[#102018] transition-colors hover:border-[#8C6D2E] hover:text-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#EFECE2] dark:hover:border-[#D4AF5F] dark:hover:text-[#E5C77B]"
                >
                  <span className="inline-flex items-center gap-1.5 truncate">
                    <MapPin className="h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                    <span className="truncate">{project.name}</span>
                  </span>
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden="true" />
                </a>
              ))}
            </div>
          </div>
        </div>
      </nav>

      {/* Keyword-Focused Buyer Acquisition Narrative — Clear, Scannable & Empathetic */}
      <section className="border-b border-black/10 bg-[#F6F5F0] py-16 sm:py-20 dark:border-white/10 dark:bg-[#090E0C] lg:py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-12 lg:gap-10">
            <div className="lg:col-span-5">
              <p className="text-xs font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
                Trusted Property in Nagpur · Clear Title First
              </p>
              <h2 className="font-display mt-3 text-balance text-3xl font-normal tracking-tight text-[#102018] sm:text-4xl lg:text-5xl dark:text-[#F4F1E7]">
                Buy verified NMRDA plots in Nagpur with zero title guesswork.
              </h2>
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-3 lg:col-span-7">
              <div className="rounded-xl border border-black/10 bg-white p-5 dark:border-white/10 dark:bg-[#101814]">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#10281E]/10 text-[#10281E] dark:bg-[#D4AF5F]/15 dark:text-[#E5C77B]">
                  <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                </span>
                <h3 className="font-display mt-3 text-2xl text-[#102018] dark:text-[#F4F1E7]">
                  Doctors &amp; Govt. Officers
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-[#4F5E55] dark:text-[#A4B3A8]">
                  Verify NMRDA layout sanction, 7/12 extract readiness, Vastu facing, and 12m–30m
                  road frontage online in 5 minutes before scheduling a private site visit.
                </p>
              </div>

              <div className="rounded-xl border border-black/10 bg-white p-5 dark:border-white/10 dark:bg-[#101814]">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#10281E]/10 text-[#10281E] dark:bg-[#D4AF5F]/15 dark:text-[#E5C77B]">
                  <TrendingUp className="h-4 w-4" aria-hidden="true" />
                </span>
                <h3 className="font-display mt-3 text-2xl text-[#102018] dark:text-[#F4F1E7]">
                  Nagpur Land Investors
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-[#4F5E55] dark:text-[#A4B3A8]">
                  Target high-growth corridors near Samruddhi Mahamarg, MIHAN SEZ, Wardha Road, and
                  Hingna with transparent Residential &amp; Commercial corner-plot pricing.
                </p>
              </div>

              <div className="rounded-xl border border-black/10 bg-white p-5 dark:border-white/10 dark:bg-[#101814]">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#10281E]/10 text-[#10281E] dark:bg-[#D4AF5F]/15 dark:text-[#E5C77B]">
                  <Home className="h-4 w-4" aria-hidden="true" />
                </span>
                <h3 className="font-display mt-3 text-2xl text-[#102018] dark:text-[#F4F1E7]">
                  Home-Building Families
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-[#4F5E55] dark:text-[#A4B3A8]">
                  Plan your dream Nagpur home calmly with our Maharashtra stamp-duty estimator,
                  women-buyer 5% duty savings, and SBI/HDFC plot-loan EMI planner.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Flagship Nagpur Estates — Separate Landing Page per Project */}
      <section
        id="estates"
        aria-labelledby="estates-title"
        className="bg-[#F6F5F0] py-16 sm:py-20 dark:bg-[#080C0A] lg:py-28"
      >
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="grid grid-cols-1 items-end gap-5 lg:grid-cols-12">
            <div className="lg:col-span-7">
              <p className="inline-flex items-center gap-1.5 text-xs font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
                <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
                <span>NMRDA Approved Layouts in Nagpur · 3D &amp; 360° Sheets</span>
              </p>
              <h2
                id="estates-title"
                className="font-display mt-3 text-balance text-3xl font-normal tracking-tight text-[#102018] sm:text-4xl lg:text-5xl dark:text-[#F4F1E7]"
              >
                Explore Nagpur residential &amp; commercial plot layouts in 3D.
              </h2>
            </div>
            <p className="text-sm leading-relaxed text-[#516057] dark:text-[#98A89D] lg:col-span-5">
              Tap any project to open its dedicated landing page, interactive 3D master map,
              360-degree individual parcel inspector, and bank plot-loan calculator.
            </p>
          </div>

          {/* Clean Minimal Govt. & Regulatory Approvals Strip (NMRDA, MahaRERA, NIT/NATP, UDCPR-2020) */}
          <div className="mt-8">
            <GovtComplianceLogos />
          </div>

          <div className="mt-10 grid grid-cols-1 gap-6 sm:gap-8 md:grid-cols-2">
            {PROJECTS.map((project) => {
              const stats = summarizeLayout(project.layout);
              return (
                <article
                  key={project.slug}
                  className="group flex flex-col overflow-hidden rounded-2xl border border-black/10 bg-white transition-transform duration-200 hover:-translate-y-1 dark:border-white/10 dark:bg-[#101814]"
                >
                  {/* Mobile-First Visual Split: Stacked cleanly on small phones, 12-col split on sm+ */}
                  <div className="grid grid-cols-1 border-b border-black/10 sm:grid-cols-12 dark:border-white/10">
                    <div className="relative h-52 overflow-hidden bg-[#0e1a14] sm:col-span-7 sm:h-64">
                      <img
                        src={project.heroImage}
                        alt={`${project.name} NMRDA residential and commercial plots in ${project.location}`}
                        width={720}
                        height={480}
                        loading="lazy"
                        decoding="async"
                        referrerPolicy="no-referrer"
                        className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-transparent" />
                      <div className="absolute right-4 bottom-3 left-4 text-xs text-white">
                        <p className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#E5C77B]">
                          <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                          <span>{project.corridor}</span>
                        </p>
                        <p className="font-mono-tabular mt-0.5 text-xs font-medium text-white">
                          {project.commercialRatePerSqFt
                            ? `Res. ${formatINR(project.baseRatePerSqFt)}* · Com. ${formatINR(project.commercialRatePerSqFt)}* / sq.ft`
                            : `From ${formatINR(project.baseRatePerSqFt)}* / sq.ft`}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center justify-center bg-[#ECEAE0] p-3 sm:col-span-5 sm:p-2.5 dark:bg-[#0C1410]">
                      <div className="pointer-events-none w-full max-w-[260px] sm:max-w-none">
                        <LayoutMap2D
                          layout={project.layout}
                          selectedPlotNo={null}
                          interactive={false}
                        />
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-1 flex-col p-5 sm:p-6">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <p className="text-xs text-[#6B7970] dark:text-[#8E9F93]">
                          {project.location} · {project.khNo}
                        </p>
                        <h3 className="font-display mt-1 text-2xl font-normal text-[#102018] sm:text-3xl dark:text-[#F4F1E7]">
                          {project.name}
                        </h3>
                      </div>
                      <ArrowUpRight
                        className="mt-1 h-5 w-5 shrink-0 text-[#8C6D2E] transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 dark:text-[#D4AF5F]"
                        aria-hidden="true"
                      />
                    </div>

                    <p className="mt-2.5 text-sm leading-relaxed text-[#4E5D54] dark:text-[#A4B3A8]">
                      {project.tagline}
                    </p>

                    {/* Clean Unboxed Inventory Metadata */}
                    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-black/10 pt-4 font-mono-tabular text-xs dark:border-white/10">
                      <span className="font-semibold text-[#2E6B42] dark:text-[#7FBB8C]">
                        {stats.available} Available
                      </span>
                      <span aria-hidden="true" className="text-[#8E9F93]">
                        ·
                      </span>
                      <span className="text-[#8C6D2E] dark:text-[#E5C77B]">
                        {stats.reserved} Reserved
                      </span>
                      <span aria-hidden="true" className="text-[#8E9F93]">
                        ·
                      </span>
                      <span className="text-[#8E4F38] dark:text-[#C57D61]">{stats.sold} Sold</span>
                      <span aria-hidden="true" className="text-[#8E9F93]">
                        ·
                      </span>
                      <span className="text-[#5C6B61] dark:text-[#98A89D]">
                        {project.frontage.split("·")[0].trim()}
                      </span>
                    </div>

                    <div className="mt-5 flex flex-wrap items-center justify-between gap-3 pt-1">
                      <a
                        href={`/projects/${project.slug}`}
                        onClick={(e) => {
                          e.preventDefault();
                          onNavigate(`/projects/${project.slug}`);
                        }}
                        className="inline-flex min-h-[44px] flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-[#10281E] px-4 py-2.5 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] sm:flex-none sm:px-5 dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                      >
                        <Compass className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span>Open 3D Map &amp; 360° Plots</span>
                        <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      </a>

                      <button
                        type="button"
                        onClick={() =>
                          onOpenLeadModal({
                            projectSlug: project.slug,
                            interest: "site-visit",
                          })
                        }
                        className="inline-flex min-h-[44px] items-center gap-1.5 whitespace-nowrap px-2 text-xs font-semibold text-[#46554C] underline-offset-4 hover:text-[#102018] hover:underline dark:text-[#B4C1B7] dark:hover:text-[#E5C77B]"
                      >
                        <CalendarCheck className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                        <span>Book Visit</span>
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          {/* Mandatory Pricing Safety Disclaimer Beneath Displayed Corridor Starting Rates */}
          <PricingDisclaimer variant="inline" className="mt-6" />
        </div>
      </section>

      {/* Cinematic 3D Scroll-Rig Spatial Journey (Code-Split for Fast Lighthouse LCP) */}
      <Suspense
        fallback={
          <div className="flex h-[540px] items-center justify-center bg-[#070E0B] text-xs tracking-wider text-[#9BB0A1]">
            Loading Interactive 3D Estate Flythrough…
          </div>
        }
      >
        <CinematicJourney3D onExploreEstates={scrollToEstates} />
      </Suspense>

      {/* Nagpur Land Budget & Home Loan EMI Calculator */}
      <BudgetLoanEstimator
        onOpenProject={(slug) => onNavigate(`/projects/${slug}`)}
        onRequestConsultation={(slug, budget) =>
          onOpenLeadModal({
            projectSlug: slug,
            budgetInr: budget,
            interest: "plot-enquiry",
          })
        }
      />

      {/* Gated Lead Magnet Field Guide — Shared Exclusively After Lead Form Submission */}
      <section className="bg-[#F6F5F0] py-16 sm:py-20 dark:bg-[#080C0A]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="grid grid-cols-1 items-center gap-8 rounded-2xl border border-[#D4AF5F]/30 bg-[#10241B] p-6 text-[#F5F2E9] sm:p-10 lg:grid-cols-12 lg:p-12 dark:bg-[#101A15]">
            <div className="lg:col-span-8">
              <p className="inline-flex items-center gap-1.5 text-xs font-semibold tracking-[0.14em] text-[#D4AF5F] uppercase">
                <FileCheck2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Free Maharashtra Plot Buyer’s Guide · Shared After Enquiry</span>
              </p>
              <h2 className="font-display mt-3 text-balance text-3xl font-normal tracking-tight text-[#F7F4EC] sm:text-4xl lg:text-5xl">
                Verify 7/12 Satbara, NMRDA Sanction &amp; Title Before You Pay a Token.
              </h2>
              <p className="mt-3.5 max-w-2xl text-sm leading-relaxed text-[#C9D6CD]">
                Submit a 30-second enquiry to unlock our 14-point Nagpur Plot Legal Checklist —
                covering 30-year title search, NA conversion orders, nala buffers, Maharashtra stamp
                duty, and Ferfar mutation.
              </p>
              <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                <button
                  type="button"
                  onClick={() => {
                    trackEvent({ type: "cta_click", page: "/", meta: "lead_magnet_form_trigger" });
                    onOpenLeadModal({ interest: "callback" });
                  }}
                  className="inline-flex min-h-[48px] items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-[#D4AF5F] px-6 py-3 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
                >
                  <FileCheck2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>Unlock 14-Point Buyer’s Guide &amp; Cost Sheet</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    trackEvent({ type: "cta_click", page: "/", meta: "lead_magnet_whatsapp" });
                    onOpenLeadModal({ interest: "callback" });
                  }}
                  className="inline-flex min-h-[48px] items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-[#25D366]/45 bg-[#0c2417]/80 px-5 py-3 text-xs font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366]"
                >
                  <WhatsAppIcon className="h-4 w-4 shrink-0 text-[#25D366]" />
                  <span>Get Guide on WhatsApp</span>
                </button>
              </div>
            </div>

            <div className="space-y-3 border-t border-white/10 pt-6 text-xs text-[#C6D2C9] lg:col-span-4 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-8">
              <div className="flex items-start gap-2.5">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                <span>30-year title chain &amp; NA conversion order verification steps</span>
              </div>
              <div className="flex items-start gap-2.5">
                <Compass className="mt-0.5 h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                <span>On-site boundary stone, road width &amp; Vastu compass checks</span>
              </div>
              <div className="flex items-start gap-2.5">
                <FileCheck2 className="mt-0.5 h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                <span>Written cost sheet &amp; MahaRERA registration verification</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Straight Answers FAQ (Optimized for AEO / Answer Engines & Organic Search) */}
      <section
        id="questions"
        aria-labelledby="faq-heading"
        className="border-t border-black/10 bg-[#ECEAE1] py-16 sm:py-20 dark:border-white/10 dark:bg-[#0B120F] lg:py-28"
      >
        <div className="mx-auto grid max-w-7xl grid-cols-1 gap-10 px-4 sm:px-6 lg:grid-cols-12 lg:gap-12 lg:px-10">
          <div className="lg:col-span-4">
            <p className="text-xs font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
              Nagpur Plot Buying FAQs · AEO Ready
            </p>
            <h2
              id="faq-heading"
              className="font-display mt-3 text-balance text-3xl font-normal tracking-tight text-[#102018] sm:text-4xl lg:text-5xl dark:text-[#F4F1E7]"
            >
              Clear answers before you invest in Nagpur land.
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-[#526158] dark:text-[#98A89D]">
              Direct facts on NMRDA approvals, Residential &amp; Commercial plot rates, bank plot
              loans, and 360° parcel verification.
            </p>
            <button
              type="button"
              onClick={() => onOpenLeadModal({ interest: "callback" })}
              className="mt-6 inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-[#10281E]/20 bg-white px-4 py-2.5 text-xs font-semibold text-[#10281E] transition-colors hover:border-[#8C6D2E] dark:border-[#25D366]/35 dark:bg-[#0d1f15] dark:text-[#7CF0A6]"
            >
              <WhatsAppIcon className="h-4 w-4 text-[#25D366]" />
              <span>Chat with a Nagpur Estate Advisor</span>
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>

          <div className="divide-y divide-black/10 border-t border-b border-black/10 dark:divide-white/10 dark:border-white/10 lg:col-span-8">
            {HOME_FAQS.map((item, idx) => (
              <details key={item.q} open={idx === 0} className="group py-5">
                <summary className="flex min-h-[44px] cursor-pointer list-none items-center justify-between gap-4 text-left">
                  <div className="flex items-baseline gap-3 sm:gap-4">
                    <span className="font-mono-tabular text-xs text-[#8C6D2E] dark:text-[#D4AF5F]">
                      0{idx + 1}
                    </span>
                    <span className="font-display text-lg text-[#102018] sm:text-2xl dark:text-[#F4F1E7]">
                      {item.q}
                    </span>
                  </div>
                  <span className="shrink-0 font-mono-tabular text-lg text-[#8C6D2E] transition-transform group-open:rotate-45 dark:text-[#D4AF5F]">
                    +
                  </span>
                </summary>
                <p className="mt-3 pr-4 pl-7 text-sm leading-relaxed text-[#4F5E55] sm:pr-6 sm:pl-9 dark:text-[#A4B3A8]">
                  {item.a}
                </p>
              </details>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
