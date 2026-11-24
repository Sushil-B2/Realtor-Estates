import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  ArrowUpRight,
  Box,
  CalendarCheck,
  ChevronDown,
  ChevronUp,
  Compass,
  Expand,
  Home,
  MapPin,
  Minus,
  Plus,
  RotateCcw,
  Satellite,
} from "lucide-react";
import {
  FACING_LABEL,
  PROJECTS,
  STATUS_META,
  summarizeLayout,
  type EstateProject,
  type PlotCell,
} from "../data/projects";
import { formatINR, formatINRCompact } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import { scrollToSmooth } from "../hooks/useSmoothScroll";
import type { MasterLayout3DHandle } from "../components/three/MasterLayout3D";
import LayoutMap2D from "../components/LayoutMap2D";
import { GovtComplianceLogos, ProjectSanctionStamp } from "../components/ComplianceAndBankLogos";
import { PricingDisclaimer, WhatsAppIcon } from "../components/PricingDisclaimer";

const MasterLayout3D = lazy(() => import("../components/three/MasterLayout3D"));
const PlotPopout3DModal = lazy(() => import("../components/three/PlotPopout3DModal"));
const NagpurGoogleMapView = lazy(() => import("../components/NagpurGoogleMapView"));

const EASE_APPLE = [0.16, 1, 0.3, 1] as const;

type Props = {
  project: EstateProject;
  initialPlotNo?: string | null;
  onNavigate: (path: string) => void;
  onOpenLeadModal: (opts: {
    projectSlug: string;
    plotNumber?: string;
    budgetInr?: number | null;
    loanRequired?: boolean;
    interest?: "site-visit" | "plot-enquiry" | "callback";
  }) => void;
};

export default function ProjectLandingPage({
  project,
  initialPlotNo,
  onNavigate,
  onOpenLeadModal,
}: Props) {
  const [statusFilter, setStatusFilter] = useState<"all" | "available" | "reserved" | "sold">("all");
  const [selectedPlot, setSelectedPlot] = useState<PlotCell | null>(null);
  const [popoutPlot, setPopoutPlot] = useState<PlotCell | null>(null);
  const [dockCollapsed, setDockCollapsed] = useState(false);
  const [mapViewMode, setMapViewMode] = useState<"3d" | "satellite">("3d");
  const [openFaqIdx, setOpenFaqIdx] = useState<number>(0);

  const sceneSectionRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<MasterLayout3DHandle | null>(null);
  const progressRef = useRef(0.16);

  const stats = summarizeLayout(project.layout);

  // Open deep-linked plot automatically if `?plot=XX` is provided in URL
  useEffect(() => {
    setStatusFilter("all");
    setSelectedPlot(null);
    setMapViewMode("3d");
    if (initialPlotNo) {
      const match = project.layout.plots.find((p) => p.no === initialPlotNo);
      if (match) {
        setSelectedPlot(match);
        setPopoutPlot(match);
      }
    } else {
      setPopoutPlot(null);
    }
  }, [project, initialPlotNo]);

  // Smooth scroll progress reader for 3D master layout spline flythrough
  useEffect(() => {
    if (mapViewMode !== "3d") return;
    let ticking = false;
    let cachedTop = 0;
    let cachedTravel = 1;

    const measure = () => {
      const el = sceneSectionRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      cachedTop = rect.top + window.scrollY;
      cachedTravel = Math.max(el.offsetHeight - window.innerHeight, 1);
    };

    const read = () => {
      ticking = false;
      progressRef.current = Math.max(0, Math.min(1, (window.scrollY - cachedTop) / cachedTravel));
    };

    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(read);
      }
    };

    const onResize = () => {
      measure();
      read();
    };

    measure();
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
    };
  }, [project.slug, mapViewMode]);

  const handlePlotClick = (plot: PlotCell) => {
    setSelectedPlot(plot);
    setPopoutPlot(plot);
    window.dispatchEvent(
      new CustomEvent("realtor-engagement", {
        detail: { kind: "plot_inspect", projectSlug: project.slug, plotNo: plot.no },
      }),
    );
  };

  const otherProjects = PROJECTS.filter((p) => p.slug !== project.slug);

  const projectJsonLd = useMemo(() => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const pageUrl = `${origin}/projects/${project.slug}`;
    return JSON.stringify({
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "Realtor Estates — Property in Nagpur", item: `${origin}/` },
            { "@type": "ListItem", position: 2, name: project.name, item: pageUrl },
          ],
        },
        {
          "@type": "RealEstateListing",
          name: `${project.name} — ${project.corridor}`,
          description: project.intro,
          url: pageUrl,
          offers: project.commercialRatePerSqFt
            ? [
                {
                  "@type": "Offer",
                  name: `${project.name} Residential Plot`,
                  price: String(project.baseRatePerSqFt),
                  priceCurrency: "INR",
                  unitText: "sq.ft",
                  availability: "https://schema.org/InStock",
                },
                {
                  "@type": "Offer",
                  name: `${project.name} Commercial Frontage Plot`,
                  price: String(project.commercialRatePerSqFt),
                  priceCurrency: "INR",
                  unitText: "sq.ft",
                  availability: "https://schema.org/InStock",
                },
              ]
            : {
                "@type": "Offer",
                name: `${project.name} Estate Plot`,
                price: String(project.baseRatePerSqFt),
                priceCurrency: "INR",
                unitText: "sq.ft",
                availability: "https://schema.org/InStock",
              },
        },
        {
          "@type": "FAQPage",
          mainEntity: project.faqs.map((faq) => ({
            "@type": "Question",
            name: faq.question,
            acceptedAnswer: { "@type": "Answer", text: faq.answer },
          })),
        },
      ],
    });
  }, [project]);

  return (
    <div>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: projectJsonLd }} />

      {/* Dedicated Estate Project Hero */}
      <section className="relative flex min-h-[76svh] items-end overflow-hidden bg-[#080C0A] text-[#F5F2E9]">
        <img
          src={project.heroImage}
          alt={`${project.name} NMRDA sanctioned plots in ${project.location}`}
          width={1600}
          height={900}
          fetchPriority="high"
          decoding="async"
          referrerPolicy="no-referrer"
          className="absolute inset-0 h-full w-full object-cover object-center"
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-r from-[#060B09]/95 via-[#060B09]/76 to-[#060B09]/25"
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-t from-[#060B09]/98 via-transparent to-[#060B09]/30"
        />

        <div className="relative z-10 mx-auto w-full max-w-7xl px-4 pt-14 pb-12 sm:px-6 sm:pt-16 sm:pb-16 lg:px-10">
          <div className="mb-6 flex flex-wrap items-center gap-2.5">
            <a
              href="/"
              onClick={(e) => {
                e.preventDefault();
                onNavigate("/");
              }}
              className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-white/20 bg-[#0a130f]/85 px-3.5 py-2 text-xs font-medium text-[#F5F2E9] transition-colors hover:border-[#D4AF5F] hover:text-[#E5C77B]"
            >
              <Home className="h-3.5 w-3.5 text-[#D4AF5F]" aria-hidden="true" />
              <span>Home</span>
            </a>
            <button
              type="button"
              onClick={() => onNavigate("/")}
              className="inline-flex min-h-[40px] items-center gap-1.5 text-xs font-medium text-[#D5DDD7] transition-colors hover:text-[#E5C77B]"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              <span>All Nagpur Estates</span>
            </button>
          </div>

          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, ease: EASE_APPLE }}
            className="max-w-2xl"
          >
            <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#E5C77B] sm:text-sm">
              <MapPin className="h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
              <span>
                {project.corridor} · {project.khNo}
              </span>
            </p>
            <h1 className="font-editorial mt-3 text-balance text-4xl leading-[0.98] font-normal tracking-tight text-white sm:text-6xl">
              {project.headline}
              <br />
              <em className="text-[#E5C77B]">{project.headlineEm}</em>
            </h1>
            <p className="mt-4 max-w-xl text-base leading-relaxed text-[#E7EEE9]">
              {project.intro}
            </p>

            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <button
                type="button"
                onClick={() => {
                  setMapViewMode("3d");
                  scrollToSmooth("#layout-3d");
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#D4AF5F] px-6 py-3 text-sm font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b] sm:w-auto"
              >
                <Compass className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>Inspect 3D Layout</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setMapViewMode("satellite");
                  scrollToSmooth("#layout-3d");
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-white/25 bg-[#0a130f]/85 px-5 py-3 text-sm font-medium text-[#F5F2E9] transition-colors hover:border-[#D4AF5F] hover:text-[#E5C77B] sm:w-auto"
              >
                <Satellite className="h-4 w-4 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                <span>Live 3D Satellite</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  trackEvent({
                    type: "cta_click",
                    page: `/projects/${project.slug}`,
                    project: project.slug,
                    meta: "project_hero_whatsapp",
                  });
                  onOpenLeadModal({ projectSlug: project.slug, interest: "callback" });
                }}
                className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-[#25D366]/45 bg-[#0c2417]/85 px-5 py-3 text-sm font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366] sm:w-auto"
              >
                <WhatsAppIcon className="h-4 w-4 shrink-0 text-[#25D366]" />
                <span>WhatsApp Cost Sheet</span>
              </button>
            </div>
          </motion.div>

          {/* Clean Inventory Metrics Strip */}
          <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-white/15 pt-5 font-mono-tabular text-xs text-[#D5DFD8] sm:gap-x-8">
            <span>
              <strong className="text-lg font-semibold text-white">{stats.total}</strong> Total Plots
            </span>
            <span>
              <strong className="text-lg font-semibold text-[#7FBB8C]">{stats.available}</strong>{" "}
              Open*
            </span>
            <span>
              <strong className="text-lg font-semibold text-[#C57D61]">{stats.sold}</strong> Sold
            </span>
            <span>
              <strong className="text-lg font-semibold text-white">
                {formatINR(project.baseRatePerSqFt)}*
              </strong>{" "}
              {project.commercialRatePerSqFt ? "Residential / sq.ft" : "/ sq.ft Ref"}
            </span>
            {project.commercialRatePerSqFt ? (
              <span>
                <strong className="text-lg font-semibold text-[#E5C77B]">
                  {formatINR(project.commercialRatePerSqFt)}*
                </strong>{" "}
                Commercial / sq.ft
              </span>
            ) : null}
          </div>
          <PricingDisclaimer variant="inline" surface="dark" className="mt-3" />
        </div>
      </section>

      {/* Interactive 3D Master Layout Explorer (Untouched) & Live 3D Satellite Toggle */}
      <section id="layout-3d" aria-labelledby="layout-3d-title" className="bg-[#09130f]">
        <div className="border-b border-white/10 bg-[#0c1612] py-10">
          <div className="mx-auto flex max-w-7xl flex-col justify-between gap-5 px-4 sm:px-6 lg:flex-row lg:items-end lg:px-10">
            <div>
              <p className="text-xs font-medium text-[#D4AF5F]">
                {mapViewMode === "3d"
                  ? "Click any plot to view in 360° with size & EMI"
                  : "Live Google Maps 3D Satellite & Nearby Places"}
              </p>
              <h2
                id="layout-3d-title"
                className="mt-2 text-2xl font-semibold tracking-tight text-[#F5F2E9] sm:text-4xl"
              >
                {project.name} — {mapViewMode === "3d" ? "3D Plot Map" : "Live 3D Satellite View"}
              </h2>
            </div>

            <div className="flex flex-col items-start gap-2.5 lg:items-end">
              <div
                role="tablist"
                aria-label="Choose estate map view"
                className="inline-flex rounded-xl border border-[#D4AF5F]/30 bg-[#080F0C] p-1"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={mapViewMode === "3d"}
                  onClick={() => setMapViewMode("3d")}
                  className={`inline-flex min-h-[40px] items-center gap-1.5 whitespace-nowrap rounded-lg px-4 py-2 text-xs font-semibold transition-colors ${
                    mapViewMode === "3d"
                      ? "bg-[#D4AF5F] text-[#080C0A]"
                      : "text-[#B5C4BA] hover:text-white"
                  }`}
                >
                  <Box className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>3D Parcel Map</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mapViewMode === "satellite"}
                  onClick={() => {
                    trackEvent({
                      type: "cta_click",
                      page: `/projects/${project.slug}`,
                      project: project.slug,
                      meta: "toggle_google_maps_satellite",
                    });
                    setMapViewMode("satellite");
                  }}
                  className={`inline-flex min-h-[40px] items-center gap-1.5 whitespace-nowrap rounded-lg px-4 py-2 text-xs font-semibold transition-colors ${
                    mapViewMode === "satellite"
                      ? "bg-[#D4AF5F] text-[#080C0A]"
                      : "text-[#B5C4BA] hover:text-white"
                  }`}
                >
                  <Satellite className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>Live 3D Satellite &amp; Nearby</span>
                </button>
              </div>
              <p className="max-w-md text-xs leading-relaxed text-[#A8B7AC] lg:text-right">
                {mapViewMode === "3d"
                  ? "Scroll or drag to explore the layout. Tap any plot for its 360° measurement and EMI view."
                  : "Orbit live satellite imagery around this site or check nearby schools, hospitals, and highways."}
              </p>
            </div>
          </div>
        </div>

        {mapViewMode === "satellite" ? (
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-10">
            <Suspense
              fallback={
                <div className="flex h-[480px] items-center justify-center rounded-3xl border border-white/10 bg-[#0b1511] text-xs text-[#9BB0A1]">
                  Loading Live 3D Satellite View…
                </div>
              }
            >
              <NagpurGoogleMapView
                project={project}
                mode="project"
                onSwitchTo3D={() => setMapViewMode("3d")}
              />
            </Suspense>
          </div>
        ) : (
          <div ref={sceneSectionRef} className="relative h-[215vh] min-h-[1450px]">
            <div className="sticky top-0 h-[100svh] min-h-[620px] w-full overflow-hidden">
              <Suspense
                fallback={
                  <div className="flex h-full w-full items-center justify-center bg-[#09130f] text-xs text-[#9BB0A1]">
                    Loading 3D Master Layout…
                  </div>
                }
              >
                <MasterLayout3D
                  ref={sceneRef}
                  layout={project.layout}
                  selectedPlotNo={selectedPlot?.no ?? null}
                  statusFilter={statusFilter}
                  onSelectPlot={handlePlotClick}
                  getScrollProgress={() => progressRef.current}
                />
              </Suspense>

              {/* Minimal Top-Left Caption */}
              <div className="pointer-events-none absolute top-4 left-6 z-10 rounded-xl border border-[#D4AF5F]/25 bg-[#0a1410]/95 px-4 py-2 text-xs text-[#E4E1D6]">
                <span className="font-semibold text-[#E5C77B]">{project.name}</span>
                <span className="mx-2 text-white/30">|</span>
                <span>Click any plot for 360° size &amp; EMI</span>
              </div>

              {/* Collapsible Bottom Control Dock */}
              <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex flex-col items-center px-4">
                <button
                  type="button"
                  onClick={() => setDockCollapsed((c) => !c)}
                  className="pointer-events-auto mb-2 inline-flex items-center gap-1.5 rounded-t-xl border border-b-0 border-[#D4AF5F]/25 bg-[#0b1511]/95 px-3.5 py-1.5 text-xs font-medium text-[#E5C77B]"
                >
                  {dockCollapsed ? (
                    <>
                      <ChevronUp className="h-3.5 w-3.5" />
                      <span>Show Controls</span>
                    </>
                  ) : (
                    <>
                      <ChevronDown className="h-3.5 w-3.5" />
                      <span>Hide Controls</span>
                    </>
                  )}
                </button>

                <div
                  className={`pointer-events-auto flex w-full max-w-[920px] flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#D4AF5F]/25 bg-[#0b1511]/95 px-4 py-3 text-xs text-[#F2EFE7] transition-all duration-300 ${
                    dockCollapsed
                      ? "pointer-events-none translate-y-24 opacity-0"
                      : "translate-y-0 opacity-100"
                  }`}
                >
                  <div
                    role="group"
                    aria-label="Filter plots by status"
                    className="flex flex-wrap items-center gap-1 rounded-xl border border-white/10 bg-[#080f0c] p-1"
                  >
                    {(["all", "available", "reserved", "sold"] as const).map((key) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setStatusFilter(key)}
                        className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                          statusFilter === key
                            ? "bg-[#D4AF5F] text-[#080C0A]"
                            : "text-[#AEBBAE] hover:text-white"
                        }`}
                      >
                        {key}
                      </button>
                    ))}
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => sceneRef.current?.setPresetAngle("aerial")}
                      className="whitespace-nowrap rounded-lg border border-white/15 bg-[#12201a] px-2.5 py-1.5 text-xs font-medium text-[#E7E3D7] hover:border-[#D4AF5F]/60"
                    >
                      Aerial
                    </button>
                    <button
                      type="button"
                      onClick={() => sceneRef.current?.setPresetAngle("isometric")}
                      className="whitespace-nowrap rounded-lg border border-white/15 bg-[#12201a] px-2.5 py-1.5 text-xs font-medium text-[#E7E3D7] hover:border-[#D4AF5F]/60"
                    >
                      Isometric
                    </button>
                    <button
                      type="button"
                      onClick={() => sceneRef.current?.setPresetAngle("top")}
                      className="whitespace-nowrap rounded-lg border border-white/15 bg-[#12201a] px-2.5 py-1.5 text-xs font-medium text-[#E7E3D7] hover:border-[#D4AF5F]/60"
                    >
                      Top Plan
                    </button>
                    <button
                      type="button"
                      aria-label="Zoom in"
                      onClick={() => sceneRef.current?.zoomBy(0.84)}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-white hover:border-[#D4AF5F]"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label="Zoom out"
                      onClick={() => sceneRef.current?.zoomBy(1.18)}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-white hover:border-[#D4AF5F]"
                    >
                      <Minus className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label="Reset camera"
                      onClick={() => sceneRef.current?.resetView()}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-white hover:border-[#D4AF5F]"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Synchronized 2D Plot Sheet & Selected Parcel Card */}
      <section className="bg-[#F6F5F0] py-20 sm:py-24 dark:bg-[#080C0A]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
            <div className="lg:col-span-8">
              <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                <div>
                  <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                    2D Layout Plan · North Up
                  </p>
                  <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-[#102018] sm:text-3xl dark:text-[#F4F1E7]">
                    Tap any plot for 360° size &amp; road view.
                  </h2>
                </div>
                <div className="flex flex-wrap items-center gap-4 text-xs text-[#4F5D54] dark:text-[#9AA99E]">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-xs bg-[#6FA07A]" /> Open
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-xs bg-[#D4AF5F]" /> Reserved
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-xs bg-[#A9654A]" /> Sold
                  </span>
                </div>
              </div>

              <LayoutMap2D
                layout={project.layout}
                selectedPlotNo={selectedPlot?.no ?? null}
                statusFilter={statusFilter}
                onSelectPlot={handlePlotClick}
              />
            </div>

            <aside className="flex flex-col justify-between rounded-3xl border border-black/[0.08] bg-white p-6 dark:border-white/[0.09] dark:bg-[#0F1713] lg:col-span-4">
              {selectedPlot ? (
                <div>
                  <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                    {selectedPlot.wing ? `${selectedPlot.wing} · ` : ""}
                    {STATUS_META[selectedPlot.status].label}
                  </p>
                  <h3 className="mt-1 text-3xl font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                    Plot {selectedPlot.displayNo ?? selectedPlot.no}
                  </h3>
                  <p className="mt-2 text-xs leading-relaxed text-[#526158] dark:text-[#9AA99E]">
                    {selectedPlot.vastuNote}
                  </p>

                  <dl className="mt-6 space-y-3 border-t border-black/[0.08] pt-5 text-xs dark:border-white/10">
                    <div className="flex justify-between">
                      <dt className="text-[#66756C] dark:text-[#8E9F93]">Dimensions</dt>
                      <dd className="font-mono-tabular font-semibold text-[#102018] dark:text-white">
                        {selectedPlot.w}m × {selectedPlot.d}m
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-[#66756C] dark:text-[#8E9F93]">Total Area</dt>
                      <dd className="font-mono-tabular font-semibold text-[#102018] dark:text-white">
                        {selectedPlot.areaSqFt.toLocaleString("en-IN")} sq.ft ({selectedPlot.areaSqM} sq.m)
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-[#66756C] dark:text-[#8E9F93]">Facing &amp; Road</dt>
                      <dd className="text-right font-semibold text-[#102018] dark:text-white">
                        {FACING_LABEL[selectedPlot.faces]}
                        <br />
                        <span className="font-normal text-[#66756C] dark:text-[#98A89D]">
                          {selectedPlot.road}
                        </span>
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-[#66756C] dark:text-[#8E9F93]">Base Estimate*</dt>
                      <dd className="font-mono-tabular font-semibold text-[#8C6D2E] dark:text-[#E5C77B]">
                        {formatINRCompact(selectedPlot.areaSqFt * selectedPlot.ratePerSqFt)}
                      </dd>
                    </div>
                  </dl>
                  <PricingDisclaimer variant="compact" className="mt-4" />
                </div>
              ) : (
                <div>
                  <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                    360° Plot Inspector
                  </p>
                  <h3 className="mt-2 text-2xl font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                    Pick any plot to inspect in 360°.
                  </h3>
                  <p className="mt-3 text-sm leading-relaxed text-[#526158] dark:text-[#9AA99E]">
                    Tap any plot number on the 2D sheet or 3D layout to check its exact dimensions,
                    front road width, Vastu direction, and monthly EMI.
                  </p>
                </div>
              )}

              <div className="mt-6 space-y-2.5">
                {selectedPlot ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setPopoutPlot(selectedPlot)}
                      className="inline-flex min-h-[46px] w-full items-center justify-center gap-2 rounded-xl bg-[#10281E] px-4 py-3 text-xs font-semibold text-[#F6F5F0] hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                    >
                      <Expand className="h-3.5 w-3.5" />
                      <span>Open 360° View &amp; EMI</span>
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        onOpenLeadModal({
                          projectSlug: project.slug,
                          plotNumber: selectedPlot.no,
                          budgetInr: selectedPlot.areaSqFt * selectedPlot.ratePerSqFt,
                          interest: "plot-enquiry",
                        })
                      }
                      className="min-h-[44px] w-full rounded-xl border border-black/15 px-4 py-2.5 text-xs font-semibold text-[#18261F] hover:border-[#8C6D2E] dark:border-white/15 dark:text-[#EFECE2] dark:hover:border-[#D4AF5F]"
                    >
                      Get Written Cost Sheet
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      const firstOpen =
                        project.layout.plots.find((p) => p.status === "available") ??
                        project.layout.plots[0];
                      if (firstOpen) handlePlotClick(firstOpen);
                    }}
                    className="inline-flex min-h-[46px] w-full items-center justify-center gap-2 rounded-xl bg-[#10281E] px-4 py-3 text-xs font-semibold text-[#F6F5F0] hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
                  >
                    <Expand className="h-3.5 w-3.5" />
                    <span>Inspect First Open Plot in 360°</span>
                  </button>
                )}
              </div>
            </aside>
          </div>
        </div>
      </section>

      {/* Project Facts, NMRDA Compliance & Nearby Landmarks */}
      <section className="border-t border-black/[0.08] bg-[#ECEAE1] py-20 sm:py-24 dark:border-white/[0.08] dark:bg-[#0B120F]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="mb-12 space-y-4">
            <ProjectSanctionStamp project={project} />
            <GovtComplianceLogos compact />
          </div>

          <div className="grid grid-cols-1 gap-10 lg:grid-cols-12">
            <div className="lg:col-span-5">
              <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                Project Highlights
              </p>
              <h2 className="mt-2 text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl dark:text-[#F4F1E7]">
                {project.name}, clearly documented.
              </h2>
              <p className="mt-4 text-sm leading-relaxed text-[#516057] dark:text-[#98A89D]">
                {project.emotionalPitch}
              </p>
              <p className="mt-4 text-xs font-medium text-[#66756C] dark:text-[#88998D]">
                Ideal for: {project.idealFor.join(" · ")}
              </p>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:col-span-7">
              {project.facts.map((fact) => (
                <div
                  key={fact.label}
                  className="rounded-3xl border border-black/[0.08] bg-white p-6 dark:border-white/[0.09] dark:bg-[#101814]"
                >
                  <p className="text-xs font-medium text-[#7C622B] dark:text-[#D4AF5F]">{fact.label}</p>
                  <p className="font-mono-tabular mt-3 text-2xl font-semibold tracking-tight text-[#102018] dark:text-white">
                    {fact.value}
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-[#55645B] dark:text-[#95A69A]">
                    {fact.note}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-6 border-t border-black/10 pt-10 dark:border-white/10 sm:grid-cols-2 lg:grid-cols-4">
            {project.landmarks.map((lm, idx) => (
              <div key={lm.name}>
                <span className="font-mono-tabular text-xs font-semibold text-[#8C6D2E] dark:text-[#D4AF5F]">
                  0{idx + 1}
                </span>
                <h3 className="mt-1.5 text-base font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                  {lm.name}
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-[#56655C] dark:text-[#92A397]">
                  {lm.note}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Project FAQs & Other Nagpur Layouts */}
      <section className="bg-[#F6F5F0] py-20 sm:py-24 dark:bg-[#080C0A]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
          <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
            <div className="lg:col-span-4">
              <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                About {project.name}
              </p>
              <h2 className="mt-2 text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl dark:text-[#F4F1E7]">
                Plain answers for this layout.
              </h2>
              <button
                type="button"
                onClick={() => onOpenLeadModal({ projectSlug: project.slug, interest: "site-visit" })}
                className="mt-6 inline-flex min-h-[46px] items-center gap-2 rounded-xl bg-[#10281E] px-5 py-3 text-xs font-semibold text-[#F6F5F0] hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
              >
                <CalendarCheck className="h-4 w-4" aria-hidden="true" />
                <span>Book Free Site Visit</span>
              </button>
            </div>

            <div className="space-y-3 lg:col-span-8">
              {project.faqs.map((faq, idx) => {
                const isOpen = openFaqIdx === idx;
                return (
                  <div
                    key={faq.question}
                    onMouseEnter={() => setOpenFaqIdx(idx)}
                    className={`overflow-hidden rounded-2xl border transition-colors duration-200 ${
                      isOpen
                        ? "border-[#8C6D2E]/55 bg-white shadow-xs dark:border-[#D4AF5F]/55 dark:bg-[#111B16]"
                        : "border-black/[0.08] bg-white/75 hover:border-black/20 dark:border-white/[0.08] dark:bg-[#0F1713] dark:hover:border-white/20"
                    }`}
                  >
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      onClick={() => setOpenFaqIdx(isOpen ? -1 : idx)}
                      className="flex min-h-[58px] w-full items-center justify-between gap-4 px-5 py-4 text-left"
                    >
                      <span className="text-base font-semibold tracking-tight text-[#102018] sm:text-lg dark:text-[#F4F1E7]">
                        {faq.question}
                      </span>
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
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.28, ease: EASE_APPLE }}
                          className="overflow-hidden"
                        >
                          <p className="border-t border-black/[0.06] px-5 pt-3.5 pb-4 text-sm leading-relaxed text-[#4F5E55] dark:border-white/[0.08] dark:text-[#A4B3A8]">
                            {faq.answer}
                          </p>
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Compare Other Nagpur Layouts */}
          <div className="mt-16 border-t border-black/10 pt-12 dark:border-white/10">
            <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              Explore Other Nagpur Estates
            </p>
            <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
              {otherProjects.map((other) => (
                <a
                  key={other.slug}
                  href={`/projects/${other.slug}`}
                  onClick={(e) => {
                    e.preventDefault();
                    onNavigate(`/projects/${other.slug}`);
                  }}
                  className="group block rounded-2xl border border-black/[0.08] bg-white p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-[#8C6D2E] dark:border-white/[0.09] dark:bg-[#0F1713] dark:hover:border-[#D4AF5F]"
                >
                  <p className="inline-flex items-center gap-1 text-xs font-medium text-[#7D632B] dark:text-[#D4AF5F]">
                    <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                    <span>{other.corridor}</span>
                  </p>
                  <div className="mt-1.5 flex items-center justify-between gap-2">
                    <h3 className="text-lg font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                      {other.name}
                    </h3>
                    <ArrowUpRight className="h-4 w-4 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  </div>
                  <p className="mt-2 text-xs text-[#57665D] dark:text-[#98A89D]">
                    {other.frontage.split("·")[0].trim()} ·{" "}
                    {other.commercialRatePerSqFt
                      ? `Res. ${formatINR(other.baseRatePerSqFt)}* / Com. ${formatINR(other.commercialRatePerSqFt)}*`
                      : `From ${formatINR(other.baseRatePerSqFt)}*/sq.ft`}
                  </p>
                </a>
              ))}
            </div>
            <PricingDisclaimer variant="inline" className="mt-4" />
          </div>
        </div>
      </section>

      {/* Individual Plot 360° Rotational Pop-Out Inspector Modal */}
      {popoutPlot ? (
        <Suspense fallback={null}>
          <PlotPopout3DModal
            project={project}
            plot={popoutPlot}
            onClose={() => setPopoutPlot(null)}
            onReservePlot={(plot, estimatedBudget, loanNeeded) => {
              setPopoutPlot(null);
              onOpenLeadModal({
                projectSlug: project.slug,
                plotNumber: plot.no,
                budgetInr: estimatedBudget,
                loanRequired: loanNeeded,
                interest: "plot-enquiry",
              });
            }}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
