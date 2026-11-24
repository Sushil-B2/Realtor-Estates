import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useScroll, useTransform } from "motion/react";
import {
  ArrowRight,
  ArrowUpRight,
  Box,
  CalendarCheck,
  CheckCircle2,
  ChevronDown,
  Compass,
  FileCheck2,
  HelpCircle,
  Landmark,
  MapPin,
  Satellite,
  ShieldCheck,
  ThumbsUp,
} from "lucide-react";
import { PROJECTS, summarizeLayout } from "../data/projects";
import { formatINR } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import { scrollToSmooth } from "../hooks/useSmoothScroll";
import PlotFinderBar from "../components/PlotFinderBar";
import BudgetLoanEstimator from "../components/BudgetLoanEstimator";
import { GovtComplianceLogos, NmrdaReraApprovalLogos } from "../components/ComplianceAndBankLogos";
import { PricingDisclaimer, WhatsAppIcon } from "../components/PricingDisclaimer";

const CinematicJourney3D = lazy(() => import("../components/three/CinematicJourney3D"));
const NagpurGoogleMapView = lazy(() => import("../components/NagpurGoogleMapView"));

type Props = {
  onNavigate: (path: string) => void;
  onOpenLeadModal: (opts: {
    projectSlug?: string;
    plotNumber?: string;
    budgetInr?: number | null;
    interest?: "site-visit" | "plot-enquiry" | "callback";
  }) => void;
};

const EASE_APPLE = [0.16, 1, 0.3, 1] as const;

type FaqCategory = "all" | "plots" | "viewer" | "loans";

const FAQ_TABS: { id: FaqCategory; label: string }[] = [
  { id: "all", label: "All Questions" },
  { id: "plots", label: "Plots & Pricing" },
  { id: "viewer", label: "3D & Satellite" },
  { id: "loans", label: "Loans & Papers" },
];

const HOME_FAQS: {
  id: string;
  category: Exclude<FaqCategory, "all">;
  tag: string;
  q: string;
  a: string;
}[] = [
  {
    id: "nmrda-plots",
    category: "plots",
    tag: "Plots & Pricing",
    q: "Which NMRDA approved plots are available in Nagpur right now?",
    a: "Choose from 4 verified Nagpur layouts: Samruddhi Estate Phase 3 & Phase 4 in Salai Dabha (Residential from ₹1,700/sq.ft*, Commercial from ₹1,750/sq.ft*), Bhagirathi Nagar - 4 in Kanholi (Residential from ₹1,990/sq.ft*, Commercial from ₹2,050/sq.ft*), and Ananta & Akasha on the 30 m road in Dongargaon.",
  },
  {
    id: "check-360",
    category: "viewer",
    tag: "3D & Satellite",
    q: "How can I check exact plot size, road width, and Vastu direction before visiting?",
    a: "Click any plot on the 3D or 2D map to open its 360° inspector. Rotate, zoom, check exact meter and feet dimensions, verify road width (9 m to 30 m), and view live compass facing directly from your phone.",
  },
  {
    id: "bank-loan",
    category: "loans",
    tag: "Loans & Papers",
    q: "Can I get a bank loan from SBI, HDFC, or ICICI for these Nagpur plots?",
    a: "Yes. NMRDA RL approved layouts with clear 7/12 (Satbara) and NA orders are eligible for plot loans from SBI, HDFC, ICICI, and Bank of Baroda, subject to standard bank eligibility checks.",
  },
  {
    id: "google-maps",
    category: "viewer",
    tag: "3D & Satellite",
    q: "Where are Ananta & Akasha and Bhagirathi Nagar - 4 located on Google Maps?",
    a: "Ananta & Akasha is directly at Satish Electricals Branch 2 (X2RH+PF, Dongargaon, Nagpur 441108). Bhagirathi Nagar - 4 is directly opposite Gurudev Zagekar Plot (X2C5+96, Kanholi, Nagpur 441108). Explore both in the Live 3D Satellite viewer above.",
  },
  {
    id: "buyers-guide",
    category: "loans",
    tag: "Loans & Papers",
    q: "How do I download the Free 14-Point Maharashtra Plot Buyer’s Guide?",
    a: "Send a quick site visit or WhatsApp enquiry anywhere on this page. You will immediately receive the 14-Point Legal Checklist covering 7/12 extracts, 30-year title checks, NMRDA sanction, and stamp duty.",
  },
];

export default function HomePage({ onNavigate, onOpenLeadModal }: Props) {
  const [satelliteMounted, setSatelliteMounted] = useState(false);
  const [faqFilter, setFaqFilter] = useState<FaqCategory>("all");
  const [openFaqId, setOpenFaqId] = useState<string>(HOME_FAQS[0].id);
  const [helpfulIds, setHelpfulIds] = useState<Record<string, boolean>>({});
  const satelliteSectionRef = useRef<HTMLElement | null>(null);
  const heroSectionRef = useRef<HTMLElement | null>(null);

  const { scrollYProgress: heroScrollProgress } = useScroll({
    target: heroSectionRef,
    offset: ["start start", "end start"],
  });
  const heroImageY = useTransform(heroScrollProgress, [0, 1], ["0%", "14%"]);
  const heroImageScale = useTransform(heroScrollProgress, [0, 1], [1, 1.06]);

  const visibleFaqs =
    faqFilter === "all" ? HOME_FAQS : HOME_FAQS.filter((item) => item.category === faqFilter);

  useEffect(() => {
    const el = satelliteSectionRef.current;
    if (!el || satelliteMounted) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setSatelliteMounted(true);
          io.disconnect();
        }
      },
      { rootMargin: "240px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [satelliteMounted]);

  const scrollToEstates = () => {
    scrollToSmooth("#estates");
  };

  return (
    <div className="overflow-x-clip">
      {/* 1. CINEMATIC HERO — Beloved Cormorant Garamond H1 + Clean Apple-Minimal Hierarchy */}
      <section
        ref={heroSectionRef}
        aria-labelledby="hero-headline"
        className="relative flex min-h-[calc(100svh-62px)] items-end overflow-hidden bg-[#060B09] text-[#F5F2E9] md:min-h-[calc(100svh-72px)]"
      >
        <motion.div
          style={{ y: heroImageY, scale: heroImageScale }}
          className="absolute inset-0 will-change-transform"
        >
          <img
            src="/website/src/assets/images/hero_nagpur_estate_1795287010697.jpg"
            alt="Twilight aerial view of NMRDA approved residential and commercial plots in Nagpur"
            width={1600}
            height={900}
            fetchPriority="high"
            decoding="async"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover object-center"
          />
        </motion.div>
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-r from-[#050A08]/95 via-[#060C09]/80 to-[#060C09]/35"
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-t from-[#050A08]/98 via-[#050A08]/45 to-[#050A08]/25"
        />

        <div className="relative z-10 mx-auto w-full max-w-7xl px-4 pt-20 pb-14 sm:px-6 sm:pb-16 lg:px-10 lg:pb-20">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: EASE_APPLE }}
            className="max-w-2xl"
          >
            <p className="text-xs font-medium text-[#E5C77B] sm:text-sm">
              NMRDA Approved Plots in Nagpur
            </p>

            {/* Beloved Cormorant Garamond Editorial Hero H1 */}
            <h1
              id="hero-headline"
              className="font-editorial mt-4 text-balance text-[3rem] leading-[0.97] font-normal tracking-tight text-white sm:text-6xl lg:text-[5rem]"
            >
              Buy Your Estates,
              <br />
              <em className="text-[#E5C77B]">Build Your Legacy.</em>
            </h1>

            <p className="mt-5 max-w-xl text-base leading-relaxed text-[#E7EEE9] sm:text-lg">
              Clear-title{" "}
              <strong className="font-semibold text-white">residential &amp; commercial plots</strong>{" "}
              in Nagpur starting at{" "}
              <strong className="font-semibold text-[#F0D78C]">₹1,700/sq.ft*</strong>. Check every
              plot’s size, road width, and live satellite location before you visit.
            </p>

            {/* Clean, Touch-Friendly Primary Actions */}
            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <button
                type="button"
                onClick={() => {
                  trackEvent({ type: "cta_click", page: "/", meta: "hero_explore_estates" });
                  scrollToEstates();
                }}
                className="inline-flex min-h-[50px] w-full items-center justify-center gap-2.5 whitespace-nowrap rounded-xl bg-[#D4AF5F] px-6 py-3 text-sm font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b] sm:w-auto"
              >
                <Compass className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>Explore Nagpur Plots</span>
                <ArrowRight className="h-4 w-4 shrink-0" aria-hidden="true" />
              </button>

              <button
                type="button"
                onClick={() => {
                  trackEvent({ type: "cta_click", page: "/", meta: "hero_book_visit" });
                  onOpenLeadModal({ interest: "site-visit" });
                }}
                className="inline-flex min-h-[50px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-white/25 bg-[#0b1511]/85 px-5 py-3 text-sm font-medium text-[#F5F2E9] transition-colors hover:border-[#D4AF5F] hover:text-[#E5C77B] sm:w-auto"
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
                className="inline-flex min-h-[50px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-[#25D366]/45 bg-[#0c2417]/85 px-5 py-3 text-sm font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366] sm:w-auto"
              >
                <WhatsAppIcon className="h-4 w-4 shrink-0 text-[#25D366]" />
                <span>WhatsApp Price List</span>
              </button>
            </div>
          </motion.div>

          {/* Quiet 3-Point Proof Row */}
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.16, ease: EASE_APPLE }}
            className="mt-12 grid grid-cols-1 gap-5 border-t border-white/15 pt-7 text-xs text-[#DCE4DE] sm:mt-14 sm:grid-cols-3 sm:gap-8"
          >
            <div className="flex items-center gap-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <Box className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold text-white">360° Plot Inspector</p>
                <p className="mt-0.5 text-[#B4C2B8]">Check size, road width &amp; Vastu facing.</p>
              </div>
            </div>
            <div className="flex items-center gap-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold text-white">NMRDA &amp; Clear 7/12</p>
                <p className="mt-0.5 text-[#B4C2B8]">Verified legal layouts with open plot status.</p>
              </div>
            </div>
            <div className="flex items-center gap-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#D4AF5F]/30 bg-[#0d1914] text-[#E5C77B]">
                <Landmark className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold text-white">Bank Loan Ready</p>
                <p className="mt-0.5 text-[#B4C2B8]">SBI, HDFC &amp; ICICI plot loan eligible*.</p>
              </div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* 2. QUICK PLOT FINDER — Filter Available Nagpur Plots in Seconds */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.15 }}
        transition={{ duration: 0.6, ease: EASE_APPLE }}
      >
        <PlotFinderBar onNavigate={onNavigate} onOpenLeadModal={onOpenLeadModal} />
      </motion.div>

      {/* 3. FOUR FLAGSHIP NAGPUR ESTATES — Spacious Apple-Clean Editorial Cards */}
      <section
        id="estates"
        aria-labelledby="estates-title"
        className="border-t border-black/[0.08] bg-[#F6F5F0] py-20 sm:py-28 dark:border-white/[0.08] dark:bg-[#080C0A] lg:py-32"
      >
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.2 }}
            transition={{ duration: 0.6, ease: EASE_APPLE }}
            className="max-w-2xl"
          >
            <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Verified Nagpur Layouts</span>
            </p>
            <h2
              id="estates-title"
              className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl lg:text-[2.75rem] lg:leading-[1.12] dark:text-[#F4F1E7]"
            >
              Four prime Nagpur estates. Ready to inspect in 3D.
            </h2>
            <p className="mt-3.5 text-base leading-relaxed text-[#4F5D54] dark:text-[#98A89D]">
              Choose a layout below to open its interactive 3D plot map, check open vs. sold plots,
              and calculate your bank EMI.
            </p>
          </motion.div>

          {/* Clean Govt. & Regulatory Approvals Strip */}
          <div className="mt-8">
            <GovtComplianceLogos />
          </div>

          <div className="mt-12 grid grid-cols-1 gap-8 md:grid-cols-2">
            {PROJECTS.map((project, index) => {
              const stats = summarizeLayout(project.layout);
              return (
                <motion.article
                  key={project.slug}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.15 }}
                  transition={{ duration: 0.55, delay: index * 0.06, ease: EASE_APPLE }}
                  className="group flex flex-col overflow-hidden rounded-3xl border border-black/[0.08] bg-white transition-all duration-300 hover:-translate-y-1 dark:border-white/[0.09] dark:bg-[#0F1713]"
                >
                  {/* Full-Bleed Architectural Photograph (No Crowded Micro-Widgets) */}
                  <a
                    href={`/projects/${project.slug}`}
                    onClick={(e) => {
                      e.preventDefault();
                      onNavigate(`/projects/${project.slug}`);
                    }}
                    className="relative aspect-[16/10] w-full overflow-hidden bg-[#0e1a14]"
                  >
                    <img
                      src={project.heroImage}
                      alt={`${project.name} residential and commercial plots in ${project.location}`}
                      width={800}
                      height={500}
                      loading="lazy"
                      decoding="async"
                      referrerPolicy="no-referrer"
                      className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/15 to-transparent" />
                    <div className="absolute right-5 bottom-4 left-5 flex items-end justify-between gap-3 text-white">
                      <div>
                        <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#E5C77B]">
                          <MapPin className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                          <span>{project.corridor}</span>
                        </p>
                        <p className="font-mono-tabular mt-1 text-sm font-semibold text-white">
                          {project.commercialRatePerSqFt
                            ? `Res. ${formatINR(project.baseRatePerSqFt)}* · Com. ${formatINR(project.commercialRatePerSqFt)}* / sq.ft`
                            : `Starting ${formatINR(project.baseRatePerSqFt)}* / sq.ft`}
                        </p>
                      </div>
                      <span className="rounded-xl bg-[#09140F]/85 px-3 py-1.5 font-mono-tabular text-xs font-semibold text-[#7FBB8C] ring-1 ring-white/15">
                        {stats.available} Open
                      </span>
                    </div>
                  </a>

                  <div className="flex flex-1 flex-col p-6 sm:p-7">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <h3 className="text-2xl font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                          {project.name}
                        </h3>
                        <p className="mt-1 text-xs text-[#64736A] dark:text-[#8E9F93]">
                          {project.location}
                        </p>
                      </div>
                      <ArrowUpRight
                        className="mt-1 h-5 w-5 shrink-0 text-[#8C6D2E] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 dark:text-[#D4AF5F]"
                        aria-hidden="true"
                      />
                    </div>

                    <p className="mt-3.5 text-sm leading-relaxed text-[#4E5D54] dark:text-[#A4B3A8]">
                      {project.tagline}
                    </p>

                    {/* Clean 3-Stat Row */}
                    <div className="mt-5 grid grid-cols-3 gap-3 border-t border-black/[0.08] pt-5 dark:border-white/10">
                      <div>
                        <p className="text-[11px] text-[#6A7970] dark:text-[#8E9F93]">Open Plots</p>
                        <p className="font-mono-tabular mt-0.5 text-sm font-semibold text-[#2E6B42] dark:text-[#7FBB8C]">
                          {stats.available} / {stats.total}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] text-[#6A7970] dark:text-[#8E9F93]">Main Road</p>
                        <p className="mt-0.5 truncate text-sm font-semibold text-[#102018] dark:text-[#F4F1E7]">
                          {project.frontage.split("·")[0].trim()}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] text-[#6A7970] dark:text-[#8E9F93]">Status</p>
                        <p className="mt-0.5 truncate text-sm font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
                          NMRDA / RL
                        </p>
                      </div>
                    </div>

                    <div className="mt-6 flex flex-wrap items-center gap-3 pt-1">
                      <a
                        href={`/projects/${project.slug}`}
                        onClick={(e) => {
                          e.preventDefault();
                          onNavigate(`/projects/${project.slug}`);
                        }}
                        className="inline-flex min-h-[46px] flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#10281E] px-5 py-2.5 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] sm:flex-none dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                      >
                        <Compass className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span>Open 3D Plot Map</span>
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
                        className="inline-flex min-h-[46px] items-center gap-1.5 whitespace-nowrap rounded-xl border border-black/10 px-4 py-2.5 text-xs font-semibold text-[#102018] transition-colors hover:border-[#8C6D2E] dark:border-white/10 dark:text-[#E4E1D6] dark:hover:border-[#D4AF5F]"
                      >
                        <CalendarCheck className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                        <span>Book Visit</span>
                      </button>
                    </div>
                  </div>
                </motion.article>
              );
            })}
          </div>

          <PricingDisclaimer variant="inline" className="mt-6" />
        </div>
      </section>

      {/* 4. LIVE 3D SATELLITE (GOD'S EYE) & NAGPUR CONNECTIVITY CENTERPIECE */}
      <section
        ref={satelliteSectionRef}
        id="nagpur-satellite-map"
        aria-labelledby="nagpur-satellite-title"
        className="border-t border-black/[0.08] bg-[#ECEAE1] py-20 sm:py-28 dark:border-white/[0.08] dark:bg-[#0B120F] lg:py-32"
      >
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.18 }}
            transition={{ duration: 0.65, ease: EASE_APPLE }}
            className="mb-8 max-w-2xl"
          >
            <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              <Satellite className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Live 3D Satellite View · Exact Nagpur Locations</span>
            </p>
            <h2
              id="nagpur-satellite-title"
              className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl lg:text-[2.75rem] lg:leading-[1.12] dark:text-[#F4F1E7]"
            >
              See every site live from the sky.
            </h2>
            <p className="mt-3.5 text-base leading-relaxed text-[#4F5D54] dark:text-[#98A89D]">
              Fly around <strong className="font-semibold text-[#102018] dark:text-white">Ananta &amp; Akasha</strong>{" "}
              (Dongargaon), <strong className="font-semibold text-[#102018] dark:text-white">Bhagirathi Nagar - 4</strong>{" "}
              (Kanholi), and <strong className="font-semibold text-[#102018] dark:text-white">Samruddhi Estate</strong>{" "}
              (Salai Dabha) on live 3D Google satellite imagery, or check nearby hospitals, schools,
              and MIHAN.
            </p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 26, scale: 0.99 }}
            whileInView={{ opacity: 1, y: 0, scale: 1 }}
            viewport={{ once: true, amount: 0.1 }}
            transition={{ duration: 0.7, ease: EASE_APPLE }}
          >
            {satelliteMounted ? (
              <Suspense
                fallback={
                  <div className="flex h-[480px] items-center justify-center rounded-3xl border border-black/10 bg-[#09130F] text-xs text-[#9BB0A1] dark:border-white/10">
                    Loading Live 3D Satellite View…
                  </div>
                }
              >
                <NagpurGoogleMapView mode="overview" onNavigate={onNavigate} />
              </Suspense>
            ) : (
              <div className="flex h-[360px] flex-col items-center justify-center rounded-3xl border border-black/10 bg-[#09130F] p-6 text-center text-xs text-[#C6D2C9] dark:border-[#D4AF5F]/20">
                <Satellite className="h-8 w-8 text-[#D4AF5F]" aria-hidden="true" />
                <p className="mt-3 text-sm font-semibold text-[#F4F1E7]">
                  Live 3D Satellite &amp; Nagpur Landmarks Ready
                </p>
                <button
                  type="button"
                  onClick={() => setSatelliteMounted(true)}
                  className="mt-4 inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-[#D4AF5F] px-5 py-2.5 text-xs font-semibold text-[#080C0A]"
                >
                  <MapPin className="h-4 w-4" aria-hidden="true" />
                  <span>Launch Live Satellite View</span>
                </button>
              </div>
            )}
          </motion.div>
        </div>
      </section>

      {/* 5. SIMPLE 3-STEP BUYING JOURNEY — Clean Apple Editorial Clarity */}
      <section className="border-t border-black/[0.08] bg-[#F6F5F0] py-20 sm:py-28 dark:border-white/[0.08] dark:bg-[#080C0A]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <motion.div
            initial={{ opacity: 0, y: 22 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.2 }}
            transition={{ duration: 0.65, ease: EASE_APPLE }}
            className="max-w-2xl"
          >
            <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              Simple, Safe Buying Process
            </p>
            <h2 className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl lg:text-[2.75rem] lg:leading-[1.12] dark:text-[#F4F1E7]">
              Buy Nagpur land with complete peace of mind.
            </h2>
          </motion.div>

          <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
            {[
              {
                step: "01",
                title: "Verify in 3D & Satellite",
                body: "Check exact plot dimensions, road width, Vastu facing, and live Google satellite roads from your home before travelling.",
              },
              {
                step: "02",
                title: "Check 7/12 & NMRDA Papers",
                body: "Review original NMRDA sanction drawings, NA orders, and clear 7/12 Satbara extracts during a guided on-site visit.",
              },
              {
                step: "03",
                title: "Easy Plot Loan & Registry",
                body: "Get a written cost sheet, apply with SBI, HDFC, or ICICI for quick plot finance, and complete smooth registration.",
              },
            ].map((item, idx) => (
              <motion.div
                key={item.step}
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.16 }}
                transition={{ duration: 0.6, delay: idx * 0.08, ease: EASE_APPLE }}
                className="rounded-3xl border border-black/[0.08] bg-white p-7 transition-transform duration-300 hover:-translate-y-1 dark:border-white/[0.09] dark:bg-[#0F1713]"
              >
                <span className="font-mono-tabular text-xs font-semibold text-[#8C6D2E] dark:text-[#D4AF5F]">
                  {item.step}
                </span>
                <h3 className="mt-3 text-xl font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                  {item.title}
                </h3>
                <p className="mt-2.5 text-sm leading-relaxed text-[#4F5E55] dark:text-[#A4B3A8]">
                  {item.body}
                </p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* 6. CINEMATIC 3D ESTATE FLYTHROUGH (Untouched 3D WebGL Scene) */}
      <Suspense
        fallback={
          <div className="flex h-[540px] items-center justify-center bg-[#070E0B] text-xs text-[#9BB0A1]">
            Loading 3D Estate Flythrough…
          </div>
        }
      >
        <CinematicJourney3D onExploreEstates={scrollToEstates} />
      </Suspense>

      {/* 7. PLOT BUDGET & LOAN EMI ESTIMATOR */}
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

      {/* 8. CLEAN COMPARISON TABLE + VISUAL BUYER'S GUIDE OFFER */}
      <section className="border-t border-black/[0.08] bg-[#F6F5F0] py-20 sm:py-28 dark:border-white/[0.08] dark:bg-[#080C0A]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          {/* Side-by-Side Comparison Table */}
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.14 }}
            transition={{ duration: 0.65, ease: EASE_APPLE }}
            className="rounded-3xl border border-black/[0.08] bg-white p-6 sm:p-8 dark:border-white/[0.09] dark:bg-[#0F1713]"
          >
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                  Quick Comparison
                </p>
                <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-[#102018] sm:text-3xl dark:text-[#F4F1E7]">
                  Compare all 4 Nagpur estates side by side.
                </h2>
              </div>
              <span className="font-mono-tabular text-xs text-[#64736A] dark:text-[#8E9F93]">
                Indicative Base Rates*
              </span>
            </div>

            <div className="mt-6 overflow-x-auto">
              <table className="w-full min-w-[660px] border-collapse text-left text-xs">
                <caption className="sr-only">
                  Comparison of Realtor Estates NMRDA residential and commercial plots in Nagpur
                </caption>
                <thead>
                  <tr className="border-b border-black/10 text-[11px] font-semibold text-[#6A7970] dark:border-white/10 dark:text-[#95A69A]">
                    <th scope="col" className="py-3 pr-4">Project Name</th>
                    <th scope="col" className="py-3 px-3">Residential*</th>
                    <th scope="col" className="py-3 px-3">Commercial*</th>
                    <th scope="col" className="py-3 px-3">Main Road</th>
                    <th scope="col" className="py-3 px-3 text-right">Open Plots</th>
                    <th scope="col" className="py-3 pl-3 text-right">3D Map</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-black/[0.08] dark:divide-white/10">
                  {PROJECTS.map((proj) => {
                    const st = summarizeLayout(proj.layout);
                    return (
                      <tr
                        key={proj.slug}
                        className="transition-colors hover:bg-[#F6F5F0]/70 dark:hover:bg-[#15221C]"
                      >
                        <th scope="row" className="py-4 pr-4 font-medium text-[#102018] dark:text-[#F4F1E7]">
                          <span className="block text-sm font-semibold">{proj.name}</span>
                          <span className="text-xs font-normal text-[#5C6B61] dark:text-[#95A69A]">
                            {proj.corridor}
                          </span>
                        </th>
                        <td className="font-mono-tabular py-4 px-3 font-semibold text-[#102018] dark:text-[#F4F1E7]">
                          {formatINR(proj.baseRatePerSqFt)}*/sq.ft
                        </td>
                        <td className="font-mono-tabular py-4 px-3 font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
                          {proj.commercialRatePerSqFt
                            ? `${formatINR(proj.commercialRatePerSqFt)}*/sq.ft`
                            : "Boulevard / Corner*"}
                        </td>
                        <td className="py-4 px-3 text-[#4A5950] dark:text-[#B4C1B7]">
                          {proj.frontage.split("·")[0].trim()}
                        </td>
                        <td className="font-mono-tabular py-4 px-3 text-right font-semibold text-[#2E6B42] dark:text-[#7FBB8C]">
                          {st.available} / {st.total}
                        </td>
                        <td className="py-4 pl-3 text-right">
                          <a
                            href={`/projects/${proj.slug}`}
                            onClick={(e) => {
                              e.preventDefault();
                              onNavigate(`/projects/${proj.slug}`);
                            }}
                            className="inline-flex items-center gap-1 font-semibold text-[#10281E] hover:text-[#8C6D2E] dark:text-[#E5C77B]"
                          >
                            <span>Inspect</span>
                            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <PricingDisclaimer variant="compact" className="mt-5" />
          </motion.div>

          {/* Gated 14-Point Buyer's Guide — Editorial Visual Split */}
          <motion.div
            initial={{ opacity: 0, y: 28 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.14 }}
            transition={{ duration: 0.68, ease: EASE_APPLE }}
            className="mt-12 grid grid-cols-1 overflow-hidden rounded-3xl border border-[#D4AF5F]/30 bg-[#10241B] text-[#F5F2E9] lg:grid-cols-12 dark:bg-[#101A15]"
          >
            <div className="flex flex-col justify-center p-7 sm:p-10 lg:col-span-7 lg:p-12">
              <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#D4AF5F]">
                <FileCheck2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Free Maharashtra Plot Buyer’s Guide</span>
              </p>
              <h2 className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                Verify 7/12 &amp; NMRDA papers before paying a token.
              </h2>
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-[#C9D6CD]">
                Unlock our concise{" "}
                <strong className="font-semibold text-white">14-Point Legal Checklist</strong>{" "}
                covering 30-year title checks, NA conversion, stamp duty, and registry steps.
              </p>

              <div className="mt-6 grid grid-cols-1 gap-3 border-t border-white/10 pt-5 text-xs text-[#D5DFD8] sm:grid-cols-3">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                  <span>7/12 &amp; 30-Yr Title</span>
                </div>
                <div className="flex items-center gap-2">
                  <Compass className="h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                  <span>Road &amp; Vastu Audit</span>
                </div>
                <div className="flex items-center gap-2">
                  <FileCheck2 className="h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                  <span>Loan &amp; Cost Sheet</span>
                </div>
              </div>

              <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                <button
                  type="button"
                  onClick={() => {
                    trackEvent({ type: "cta_click", page: "/", meta: "lead_magnet_form_trigger" });
                    onOpenLeadModal({ interest: "callback" });
                  }}
                  className="inline-flex min-h-[48px] items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#D4AF5F] px-6 py-3 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
                >
                  <FileCheck2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>Get Free Buyer’s Guide</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    trackEvent({ type: "cta_click", page: "/", meta: "lead_magnet_whatsapp" });
                    onOpenLeadModal({ interest: "callback" });
                  }}
                  className="inline-flex min-h-[48px] items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-[#25D366]/45 bg-[#0c2417]/80 px-5 py-3 text-xs font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366]"
                >
                  <WhatsAppIcon className="h-4 w-4 shrink-0 text-[#25D366]" />
                  <span>Send on WhatsApp</span>
                </button>
              </div>
            </div>

            {/* Real Editorial Estate Planning Photograph */}
            <div className="group relative min-h-[250px] overflow-hidden bg-[#0a1510] sm:min-h-[300px] lg:col-span-5 lg:min-h-full">
              <img
                src="/website/src/assets/images/nagpur_legal_checklist_desk_1795427486727.jpg"
                alt="Architectural layout plan and brass compass prepared for Nagpur plot legal inspection"
                width={900}
                height={675}
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-[#09140f]/85 via-[#09140f]/20 to-transparent lg:bg-gradient-to-r lg:from-[#10241B]/65 lg:via-transparent lg:to-transparent dark:lg:from-[#101A15]/65" />
              <div className="absolute right-5 bottom-4 left-5 flex items-center justify-between gap-3 rounded-2xl border border-white/15 bg-[#09140F]/85 px-4 py-3 text-xs text-[#EFECE2]">
                <div>
                  <p className="font-semibold text-white">14-Point Legal Checklist</p>
                  <p className="mt-0.5 text-[11px] text-[#B8C6BC]">
                    Instant WhatsApp PDF · Maharashtra NMRDA Guide
                  </p>
                </div>
                <FileCheck2 className="h-5 w-5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
              </div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* 9. INTERACTIVE FAQS WITH ESTATE PHOTOGRAPHY */}
      <section
        id="questions"
        aria-labelledby="faq-heading"
        className="border-t border-black/[0.08] bg-[#ECEAE1] py-20 sm:py-28 dark:border-white/[0.08] dark:bg-[#0B120F]"
      >
        <div className="mx-auto grid max-w-7xl grid-cols-1 gap-10 px-4 sm:px-6 lg:grid-cols-12 lg:gap-12 lg:px-10">
          {/* Left Visual & Direct Advisor Column */}
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.16 }}
            transition={{ duration: 0.65, ease: EASE_APPLE }}
            className="flex flex-col justify-between gap-6 lg:col-span-5"
          >
            <div>
              <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Interactive Buyer Help Desk</span>
              </p>
              <h2
                id="faq-heading"
                className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl dark:text-[#F4F1E7]"
              >
                Clear answers before you buy.
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-[#526158] dark:text-[#98A89D]">
                Pick a topic, hover, or tap any question to get straight, plain-English answers.
              </p>
            </div>

            {/* NMRDA Sanctioned & MahaRERA Approved Logo Showcase + Direct Advisor Action */}
            <div className="rounded-3xl border border-[#D4AF5F]/30 bg-[#10241B] p-5 text-white shadow-sm dark:bg-[#0D1612]">
              <NmrdaReraApprovalLogos />
              <div className="mt-4 rounded-2xl border border-white/12 bg-[#09140F] p-4">
                <p className="text-xs font-semibold text-white">
                  Need help choosing the right Nagpur plot?
                </p>
                <p className="mt-1 text-[11px] text-[#B8C7BD]">
                  Speak directly with our layout advisor for open plots &amp; site visit timings.
                </p>
                <button
                  type="button"
                  onClick={() => onOpenLeadModal({ interest: "callback" })}
                  className="mt-3 inline-flex min-h-[40px] items-center gap-2 rounded-xl bg-[#D4AF5F] px-4 py-2 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
                >
                  <WhatsAppIcon className="h-3.5 w-3.5 text-[#080C0A]" />
                  <span>Chat on WhatsApp</span>
                  <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            </div>
          </motion.div>

          {/* Right Interactive Filterable Accordion Column */}
          <motion.div
            initial={{ opacity: 0, y: 26 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.12 }}
            transition={{ duration: 0.65, delay: 0.08, ease: EASE_APPLE }}
            className="lg:col-span-7"
          >
            {/* Interactive Topic Filter Bar */}
            <div
              role="tablist"
              aria-label="Filter buyer questions by topic"
              className="flex flex-wrap gap-2"
            >
              {FAQ_TABS.map((tab) => {
                const active = faqFilter === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => {
                      setFaqFilter(tab.id);
                      const nextList =
                        tab.id === "all"
                          ? HOME_FAQS
                          : HOME_FAQS.filter((f) => f.category === tab.id);
                      if (nextList.length > 0 && !nextList.some((f) => f.id === openFaqId)) {
                        setOpenFaqId(nextList[0].id);
                      }
                    }}
                    className={`relative min-h-[40px] rounded-xl px-4 py-2 text-xs font-semibold transition-colors ${
                      active
                        ? "text-[#F6F5F0] dark:text-[#080C0A]"
                        : "border border-black/10 bg-white text-[#46564C] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#A9B8AE] dark:hover:border-[#D4AF5F]"
                    }`}
                  >
                    {active ? (
                      <motion.span
                        layoutId="faq-filter-pill"
                        transition={{ type: "spring", stiffness: 380, damping: 32 }}
                        className="absolute inset-0 rounded-xl bg-[#10281E] dark:bg-[#D4AF5F]"
                      />
                    ) : null}
                    <span className="relative z-10">{tab.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Interactive Animated Accordion Cards */}
            <div className="mt-5 space-y-3">
              {visibleFaqs.map((item, idx) => {
                const isOpen = openFaqId === item.id;
                const isHelpful = Boolean(helpfulIds[item.id]);
                return (
                  <motion.div
                    key={item.id}
                    layout
                    onMouseEnter={() => setOpenFaqId(item.id)}
                    transition={{ duration: 0.3, ease: EASE_APPLE }}
                    className={`overflow-hidden rounded-2xl border transition-colors duration-200 ${
                      isOpen
                        ? "border-[#8C6D2E]/55 bg-white shadow-sm dark:border-[#D4AF5F]/55 dark:bg-[#111B16]"
                        : "border-black/[0.08] bg-white/80 hover:border-black/20 dark:border-white/[0.08] dark:bg-[#0F1713] dark:hover:border-white/20"
                    }`}
                  >
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-controls={`faq-panel-${item.id}`}
                      onClick={() => setOpenFaqId(isOpen ? "" : item.id)}
                      className="flex min-h-[60px] w-full items-center justify-between gap-4 px-5 py-4 text-left"
                    >
                      <div className="flex items-start gap-3.5">
                        <span
                          className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg font-mono-tabular text-[11px] font-semibold transition-colors ${
                            isOpen
                              ? "bg-[#10281E] text-[#E5C77B] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                              : "bg-[#EFECE3] text-[#7A6027] dark:bg-[#17241E] dark:text-[#D4AF5F]"
                          }`}
                        >
                          0{idx + 1}
                        </span>
                        <div>
                          <span className="text-sm font-semibold tracking-tight text-[#102018] sm:text-base dark:text-[#F4F1E7]">
                            {item.q}
                          </span>
                          <span className="mt-1 block text-[11px] font-medium text-[#7A6027] dark:text-[#C9A85B]">
                            {item.tag}
                          </span>
                        </div>
                      </div>
                      <ChevronDown
                        className={`h-4 w-4 shrink-0 text-[#8C6D2E] transition-transform duration-300 dark:text-[#D4AF5F] ${
                          isOpen ? "rotate-180" : ""
                        }`}
                        aria-hidden="true"
                      />
                    </button>

                    <AnimatePresence initial={false}>
                      {isOpen ? (
                        <motion.div
                          id={`faq-panel-${item.id}`}
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.32, ease: EASE_APPLE }}
                          className="overflow-hidden"
                        >
                          <div className="border-t border-black/[0.06] px-5 pt-3.5 pb-4 pl-[3.75rem] dark:border-white/[0.08]">
                            <p className="text-sm leading-relaxed text-[#46554C] dark:text-[#AEBCB2]">
                              {item.a}
                            </p>

                            {/* Interactive Micro-Feedback & Quick Advisor Prompt */}
                            <div className="mt-4 flex flex-wrap items-center justify-between gap-2.5 border-t border-black/[0.06] pt-3 dark:border-white/[0.08]">
                              <button
                                type="button"
                                onClick={() =>
                                  setHelpfulIds((prev) => ({
                                    ...prev,
                                    [item.id]: !prev[item.id],
                                  }))
                                }
                                className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-colors ${
                                  isHelpful
                                    ? "bg-[#E6F4EB] text-[#1E6337] dark:bg-[#152B1E] dark:text-[#7CF0A6]"
                                    : "bg-[#F4F2EA] text-[#536359] hover:text-[#102018] dark:bg-[#16221C] dark:text-[#98A89D] dark:hover:text-white"
                                }`}
                              >
                                {isHelpful ? (
                                  <>
                                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                                    <span>Glad this helped!</span>
                                  </>
                                ) : (
                                  <>
                                    <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" />
                                    <span>Helpful</span>
                                  </>
                                )}
                              </button>

                              <button
                                type="button"
                                onClick={() => onOpenLeadModal({ interest: "callback" })}
                                className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#8C6D2E] hover:underline dark:text-[#E5C77B]"
                              >
                                <span>Ask Advisor about this</span>
                                <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                              </button>
                            </div>
                          </div>
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        </div>
      </section>
    </div>
  );
}
