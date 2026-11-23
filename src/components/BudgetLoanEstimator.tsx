import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight,
  Calculator,
  Compass,
  ExternalLink,
  Layers,
  Map as MapIcon,
  MapPin,
  Minus,
  Plus,
  Satellite,
} from "lucide-react";
import { PROJECTS } from "../data/projects";
import { calculatePlotFinance, formatINR, formatINRCompact } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import { LoanPartnerBanks } from "./ComplianceAndBankLogos";
import { PricingDisclaimer } from "./PricingDisclaimer";

const EASE_APPLE = [0.16, 1, 0.3, 1] as const;

type VisualMode = "aerial" | "esri" | "osm";

type ProjectVisualConfig = {
  aerialImage: string;
  aerialAlt: string;
  lat: number;
  lng: number;
  siteCaption: string;
};

// Unique high-angle 3D aerial layout render + exact Nagpur site coordinates for third-party Esri / OpenStreetMap imagery
const PROJECT_VISUALS: Record<string, ProjectVisualConfig> = {
  "samruddhi-estate-phase-3": {
    aerialImage: "/src/assets/images/aerial_samruddhi_phase3_1795439468816.jpg",
    aerialAlt:
      "High-angle 3D aerial layout view of Samruddhi Estate Phase 3 residential and commercial plots in Salai Dabha, Nagpur",
    lat: 21.1488,
    lng: 78.9942,
    siteCaption: "Salai Dabha · 12 m Spine & 9 m Concrete Roads",
  },
  "samruddhi-estate-phase-4": {
    aerialImage: "/src/assets/images/aerial_samruddhi_phase4_1795439461528.jpg",
    aerialAlt:
      "High-angle 3D aerial masterplan view of Samruddhi Estate Phase 4 fronting the 18 m D.P. Road in Salai Dabha, Nagpur",
    lat: 21.1516,
    lng: 78.9968,
    siteCaption: "Salai Dabha · 18 m D.P. Road Commercial Frontage",
  },
  "bhagirath-nagari": {
    aerialImage: "/src/assets/images/aerial_bhagirathi_nagar4_1795439468250.jpg",
    aerialAlt:
      "High-angle bird's-eye 3D aerial masterplan view of Bhagirathi Nagar - 4 15.69-acre plotted township in Kanholi, Nagpur",
    lat: 20.9709,
    lng: 79.0092,
    siteCaption: "Mouza Kanholi · Opp. Gurudev Zagekar Plot (X2C5+96)",
  },
  "ananta-akasha": {
    aerialImage: "/src/assets/images/aerial_ananta_akasha_1795439468402.jpg",
    aerialAlt:
      "High-angle 3D aerial view of Ananta and Akasha boutique plotted estate fronting 30 m highway in Dongargaon, Nagpur",
    lat: 20.9918,
    lng: 79.0287,
    siteCaption: "Dongargaon · Satish Electricals Branch 2 (X2RH+PF)",
  },
};

function latLngToTileCoords(lat: number, lng: number, zoom: number) {
  const n = Math.pow(2, zoom);
  const xFloat = ((lng + 180) / 360) * n;
  const latRad = (lat * Math.PI) / 180;
  const yFloat =
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  const xTile = Math.floor(xFloat);
  const yTile = Math.floor(yFloat);
  return {
    xTile,
    yTile,
    dx: xFloat - xTile,
    dy: yFloat - yTile,
  };
}

function ThirdPartyTileMap({
  lat,
  lng,
  zoom,
  mode,
  fallbackImage,
  projectName,
}: {
  lat: number;
  lng: number;
  zoom: number;
  mode: "esri" | "osm";
  fallbackImage: string;
  projectName: string;
}) {
  const [tileError, setTileError] = useState(false);
  const { xTile, yTile, dx, dy } = useMemo(
    () => latLngToTileCoords(lat, lng, zoom),
    [lat, lng, zoom],
  );

  const offsets = [-1, 0, 1];
  const centerPxX = (1 + dx) * 256;
  const centerPxY = (1 + dy) * 256;

  if (tileError) {
    return (
      <img
        src={fallbackImage}
        alt={`Aerial overview of ${projectName} in Nagpur`}
        className="h-full w-full object-cover"
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#0c1611]">
      <div
        className="absolute top-1/2 left-1/2 grid h-[768px] w-[768px] grid-cols-3 grid-rows-3"
        style={{
          transform: `translate(${-centerPxX}px, ${-centerPxY}px)`,
        }}
      >
        {offsets.map((dyOff) =>
          offsets.map((dxOff) => {
            const tx = xTile + dxOff;
            const ty = yTile + dyOff;
            const tileUrl =
              mode === "esri"
                ? `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${zoom}/${ty}/${tx}`
                : `https://tile.openstreetmap.org/${zoom}/${tx}/${ty}.png`;

            return (
              <img
                key={`${mode}-${zoom}-${tx}-${ty}`}
                src={tileUrl}
                alt=""
                width={256}
                height={256}
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={() => setTileError(true)}
                className="block h-[256px] w-[256px] select-none object-cover"
              />
            );
          }),
        )}
      </div>

      {/* Crisp Site Crosshair & Location Marker */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
      >
        <span className="flex h-11 w-11 items-center justify-center rounded-full border-2 border-[#E5C77B] bg-[#0B1712]/45 shadow-lg">
          <span className="h-2.5 w-2.5 rounded-full bg-[#E5C77B] ring-4 ring-[#E5C77B]/35" />
        </span>
      </div>
    </div>
  );
}

type Props = {
  onOpenProject: (slug: string) => void;
  onRequestConsultation: (projectSlug: string, budgetInr: number) => void;
};

export default function BudgetLoanEstimator({ onOpenProject, onRequestConsultation }: Props) {
  const [selectedSlug, setSelectedSlug] = useState(PROJECTS[0].slug);
  const [visualMode, setVisualMode] = useState<VisualMode>("aerial");
  const [mapZoom, setMapZoom] = useState(16);
  const [plotUse, setPlotUse] = useState<"residential" | "commercial">("residential");
  const [areaSqFt, setAreaSqFt] = useState(1800);
  const [downPaymentPct, setDownPaymentPct] = useState(25);
  const [interestRate, setInterestRate] = useState(8.85);
  const [tenureYears, setTenureYears] = useState(15);
  const [cornerOrPark, setCornerOrPark] = useState(false);
  const [womenConcession, setWomenConcession] = useState(false);

  const project = PROJECTS.find((p) => p.slug === selectedSlug) ?? PROJECTS[0];
  const visualMeta = PROJECT_VISUALS[project.slug] ?? PROJECT_VISUALS[PROJECTS[0].slug];
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

  const openStreetMapLink = `https://www.openstreetmap.org/?mlat=${visualMeta.lat}&mlon=${visualMeta.lng}#map=${mapZoom}/${visualMeta.lat}/${visualMeta.lng}`;

  return (
    <section
      id="estimator"
      aria-labelledby="estimator-title"
      className="border-t border-black/[0.08] bg-[#ECEAE1] py-20 sm:py-24 dark:border-white/[0.08] dark:bg-[#0B120F] lg:py-28"
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
        {/* Editorial Visual Split Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.16 }}
          transition={{ duration: 0.65, ease: EASE_APPLE }}
          className="grid grid-cols-1 items-center gap-8 lg:grid-cols-12 lg:gap-10"
        >
          <div className="lg:col-span-6">
            <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              <Calculator className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Plot Budget &amp; Loan Calculator</span>
            </p>
            <h2
              id="estimator-title"
              className="mt-2.5 text-balance text-3xl font-semibold tracking-tight text-[#102018] sm:text-4xl lg:text-[2.75rem] lg:leading-[1.12] dark:text-[#F4F1E7]"
            >
              Check your plot cost and monthly EMI.
            </h2>
            <p className="mt-3 max-w-xl text-base leading-relaxed text-[#4F5D54] dark:text-[#9AA99E]">
              Pick a Nagpur layout to inspect its{" "}
              <strong className="font-semibold text-[#102018] dark:text-[#F4F1E7]">
                3D aerial masterplan &amp; live Esri satellite view
              </strong>{" "}
              while calculating stamp duty and bank loan EMI.
            </p>

            {/* Clean Project Segmented Tabs */}
            <div
              role="group"
              aria-label="Choose Nagpur project"
              className="mt-6 flex flex-wrap gap-2"
            >
              {PROJECTS.map((item) => (
                <button
                  key={item.slug}
                  type="button"
                  onClick={() => {
                    setSelectedSlug(item.slug);
                    trackEvent({ type: "calculator_update", page: "/", project: item.slug });
                    window.dispatchEvent(
                      new CustomEvent("realtor-engagement", {
                        detail: { kind: "emi_adjust", projectSlug: item.slug },
                      }),
                    );
                  }}
                  className={`min-h-[42px] whitespace-nowrap rounded-xl border px-4 py-2 text-xs font-semibold transition-all duration-200 ${
                    selectedSlug === item.slug
                      ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] shadow-sm dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                      : "border-black/10 bg-white text-[#3F4E45] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#111A16] dark:text-[#B8C4BB] dark:hover:border-[#D4AF5F]"
                  }`}
                >
                  {item.name}
                </button>
              ))}
            </div>
          </div>

          {/* Per-Project High-Angle 3D Aerial Layout + Third-Party Esri Satellite / OpenStreetMap Viewer */}
          <div className="lg:col-span-6">
            <div className="overflow-hidden rounded-3xl border border-black/[0.08] bg-[#0e1914] shadow-sm dark:border-white/10">
              {/* Top Mode Switcher Bar (Apple HIG Segmented Control) */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-[#0A130F] px-3.5 py-2.5">
                <div
                  role="tablist"
                  aria-label="Choose aerial plan or third-party satellite map"
                  className="inline-flex rounded-xl bg-white/[0.07] p-0.5 text-[11px]"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={visualMode === "aerial"}
                    onClick={() => setVisualMode("aerial")}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-semibold transition-colors ${
                      visualMode === "aerial"
                        ? "bg-[#D4AF5F] text-[#080C0A]"
                        : "text-[#C6D2C9] hover:text-white"
                    }`}
                  >
                    <Layers className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>3D Aerial Plan</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={visualMode === "esri"}
                    onClick={() => setVisualMode("esri")}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-semibold transition-colors ${
                      visualMode === "esri"
                        ? "bg-[#D4AF5F] text-[#080C0A]"
                        : "text-[#C6D2C9] hover:text-white"
                    }`}
                  >
                    <Satellite className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Esri Satellite</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={visualMode === "osm"}
                    onClick={() => setVisualMode("osm")}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-semibold transition-colors ${
                      visualMode === "osm"
                        ? "bg-[#D4AF5F] text-[#080C0A]"
                        : "text-[#C6D2C9] hover:text-white"
                    }`}
                  >
                    <MapIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>OpenStreetMap</span>
                  </button>
                </div>

                {visualMode !== "aerial" ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setMapZoom((z) => Math.max(14, z - 1))}
                      aria-label="Zoom out satellite map"
                      className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/15 bg-white/5 text-white transition-colors hover:border-[#D4AF5F]"
                    >
                      <Minus className="h-3.5 w-3.5" />
                    </button>
                    <span className="font-mono-tabular px-1.5 text-[10px] font-medium text-[#B8C6BC]">
                      Z{mapZoom}
                    </span>
                    <button
                      type="button"
                      onClick={() => setMapZoom((z) => Math.min(18, z + 1))}
                      aria-label="Zoom in satellite map"
                      className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/15 bg-white/5 text-white transition-colors hover:border-[#D4AF5F]"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                    <a
                      href={openStreetMapLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Open ${project.name} in OpenStreetMap`}
                      className="ml-1 flex h-7 w-7 items-center justify-center rounded-lg border border-white/15 bg-white/5 text-[#E5C77B] transition-colors hover:border-[#D4AF5F]"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </div>
                ) : (
                  <span className="font-mono-tabular text-[10px] font-medium text-[#A9B9AE]">
                    High-Angle 3D Layout
                  </span>
                )}
              </div>

              {/* Dynamic Per-Project Viewport */}
              <div className="group relative aspect-[16/10] w-full overflow-hidden bg-[#0c1611]">
                <AnimatePresence mode="wait">
                  {visualMode === "aerial" ? (
                    <motion.div
                      key={`aerial-${project.slug}`}
                      initial={{ opacity: 0, scale: 1.02 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.35, ease: EASE_APPLE }}
                      className="h-full w-full"
                    >
                      <img
                        src={visualMeta.aerialImage}
                        alt={visualMeta.aerialAlt}
                        width={960}
                        height={600}
                        loading="lazy"
                        decoding="async"
                        referrerPolicy="no-referrer"
                        className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]"
                      />
                    </motion.div>
                  ) : (
                    <motion.div
                      key={`${visualMode}-${project.slug}-${mapZoom}`}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.25 }}
                      className="h-full w-full"
                    >
                      <ThirdPartyTileMap
                        lat={visualMeta.lat}
                        lng={visualMeta.lng}
                        zoom={mapZoom}
                        mode={visualMode}
                        fallbackImage={visualMeta.aerialImage}
                        projectName={project.name}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>

                <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-[#07100c]/90 via-[#07100c]/15 to-transparent" />

                <div className="absolute right-4 bottom-3.5 left-4 flex items-end justify-between gap-3 text-white">
                  <div className="min-w-0">
                    <p className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[#E5C77B]">
                      <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                      <span className="truncate">{visualMeta.siteCaption}</span>
                    </p>
                    <p className="mt-0.5 truncate text-sm font-semibold text-white">
                      {project.name}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span className="rounded-xl bg-[#0a1410]/88 px-3 py-1.5 font-mono-tabular text-xs font-semibold text-[#E5C77B] ring-1 ring-white/15">
                      {formatINR(activeRatePerSqFt)}*/sq.ft
                    </span>
                    {visualMode !== "aerial" ? (
                      <span className="text-[9px] text-[#C6D2C9]/85">
                        {visualMode === "esri" ? "Imagery © Esri World Imagery" : "Map © OpenStreetMap"}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 26 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.12 }}
          transition={{ duration: 0.65, delay: 0.08, ease: EASE_APPLE }}
          className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-12"
        >
          {/* Controls Column */}
          <div className="space-y-6 rounded-3xl border border-black/[0.08] bg-white p-6 dark:border-white/[0.09] dark:bg-[#101814] sm:p-8 lg:col-span-7">
            {project.commercialRatePerSqFt ? (
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/[0.08] pb-5 dark:border-white/10">
                <span className="text-sm font-medium text-[#18261F] dark:text-[#E8E5DA]">
                  Plot Category
                </span>
                <div
                  role="group"
                  aria-label="Choose residential or commercial rate"
                  className="inline-flex rounded-xl border border-black/10 bg-[#F4F3EE] p-1 text-xs dark:border-white/10 dark:bg-[#0B120F]"
                >
                  <button
                    type="button"
                    onClick={() => setPlotUse("residential")}
                    className={`whitespace-nowrap rounded-lg px-3.5 py-2 font-semibold transition-colors ${
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
                    className={`whitespace-nowrap rounded-lg px-3.5 py-2 font-semibold transition-colors ${
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
                <label htmlFor="est-area" className="text-sm font-semibold text-[#18261F] dark:text-[#EFECE2]">
                  Plot Size
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
                onChange={(e) => {
                  setAreaSqFt(Number(e.target.value));
                  window.dispatchEvent(
                    new CustomEvent("realtor-engagement", {
                      detail: { kind: "emi_adjust", projectSlug: selectedSlug },
                    }),
                  );
                }}
                className="estate-slider mt-3 w-full"
              />
              <div className="mt-2 flex justify-between font-mono-tabular text-[11px] text-[#6A7970] dark:text-[#84958A]">
                <span>1,000 sq.ft</span>
                <span>Rate: {formatINR(activeRatePerSqFt)}/sq.ft*</span>
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

            <div className="grid grid-cols-1 gap-5 border-t border-black/[0.08] pt-5 dark:border-white/10 sm:grid-cols-2">
              <div>
                <div className="flex items-baseline justify-between">
                  <label htmlFor="est-roi" className="text-sm font-medium text-[#18261F] dark:text-[#EFECE2]">
                    Bank Interest Rate
                  </label>
                  <span className="font-mono-tabular text-sm font-semibold text-[#18261F] dark:text-white">
                    {interestRate.toFixed(2)}% p.a.
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

              <div className="flex flex-col justify-center gap-3 text-xs">
                <label className="flex cursor-pointer items-center gap-2.5 text-[#3F4E45] dark:text-[#B9C5BC]">
                  <input
                    type="checkbox"
                    checked={cornerOrPark}
                    onChange={(e) => setCornerOrPark(e.target.checked)}
                    className="h-4 w-4 accent-[#D4AF5F]"
                  />
                  <span>Corner / Park Facing (+5% PLC)</span>
                </label>
                <label className="flex cursor-pointer items-center gap-2.5 text-[#3F4E45] dark:text-[#B9C5BC]">
                  <input
                    type="checkbox"
                    checked={womenConcession}
                    onChange={(e) => setWomenConcession(e.target.checked)}
                    className="h-4 w-4 accent-[#D4AF5F]"
                  />
                  <span>Women Buyer Discount (5% Stamp Duty)</span>
                </label>
              </div>
            </div>
          </div>

          {/* Live Breakdown Output Column */}
          <div className="flex flex-col justify-between rounded-3xl border border-[#D4AF5F]/30 bg-[#10241B] p-6 text-[#F4F1E7] dark:bg-[#111D17] sm:p-8 lg:col-span-5">
            <div>
              <p className="text-xs font-medium text-[#D4AF5F]">
                Estimated Monthly EMI*
              </p>
              <p className="font-mono-tabular mt-2 text-4xl font-semibold tracking-tight text-[#F7F4EC]">
                {formatINR(result.monthlyEmi)}
                <span className="ml-1.5 text-sm font-normal text-[#A8B9AE]">/ month</span>
              </p>

              {/* Principal vs Interest Proportion Bar */}
              <div className="mt-6">
                <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full bg-[#D4AF5F] transition-all duration-300"
                    style={{ width: `${principalSharePct}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between font-mono-tabular text-[11px] text-[#AEBBAE]">
                  <span>Loan Principal {principalSharePct}%</span>
                  <span>Interest {100 - principalSharePct}%</span>
                </div>
              </div>

              <dl className="mt-6 space-y-3 border-t border-white/10 pt-5 text-xs">
                <div className="flex justify-between">
                  <dt className="text-[#A8B8AD]">Plot Base Cost</dt>
                  <dd className="font-mono-tabular font-medium text-white">{formatINR(result.baseCost)}</dd>
                </div>
                {result.plcAmount > 0 ? (
                  <div className="flex justify-between">
                    <dt className="text-[#A8B8AD]">Corner / Park PLC</dt>
                    <dd className="font-mono-tabular font-medium text-white">{formatINR(result.plcAmount)}</dd>
                  </div>
                ) : null}
                <div className="flex justify-between">
                  <dt className="text-[#A8B8AD]">
                    Stamp Duty ({result.stampDutyRate}%) + Registration
                  </dt>
                  <dd className="font-mono-tabular font-medium text-white">
                    {formatINR(result.stampDuty + result.registrationFee)}
                  </dd>
                </div>
                <div className="flex justify-between border-t border-white/10 pt-3 text-sm font-semibold">
                  <dt className="text-[#E5C77B]">Total Estimated Budget*</dt>
                  <dd className="font-mono-tabular text-[#E5C77B]">
                    {formatINRCompact(result.totalAcquisitionCost)}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="mt-7 flex flex-col gap-2.5">
              <button
                type="button"
                onClick={() => onRequestConsultation(project.slug, result.totalAcquisitionCost)}
                className="min-h-[48px] w-full rounded-xl bg-[#D4AF5F] px-4 py-3 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
              >
                Get Official Cost Sheet &amp; Loan Check
              </button>
              <button
                type="button"
                onClick={() => onOpenProject(project.slug)}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-white/15 px-4 py-2.5 text-xs font-medium text-[#EFECE2] transition-colors hover:border-[#D4AF5F]"
              >
                <Compass className="h-3.5 w-3.5 text-[#D4AF5F]" aria-hidden="true" />
                <span>Explore {project.name} in 3D</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        </motion.div>

        {/* Mandatory Pricing Disclaimer */}
        <PricingDisclaimer variant="default" className="mt-6" />

        {/* Partner Bank Logos for Nagpur Plot Loans */}
        <LoanPartnerBanks variant="section" />
      </div>
    </section>
  );
}
