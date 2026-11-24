import { useMemo, useState } from "react";
import { ArrowRight, Compass, MapPin, RotateCcw, SlidersHorizontal, Sparkles } from "lucide-react";
import { FACING_LABEL, PROJECTS, type Facing } from "../data/projects";
import { formatINRCompact } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import { PricingDisclaimer, WhatsAppIcon } from "./PricingDisclaimer";

type Props = {
  onNavigate: (path: string) => void;
  onOpenLeadModal: (opts: {
    projectSlug?: string;
    plotNumber?: string;
    budgetInr?: number | null;
    interest?: "site-visit" | "plot-enquiry" | "callback";
  }) => void;
};

type BudgetCap = "any" | "2500000" | "3500000" | "5000000" | "7500000";

export default function PlotFinderBar({ onNavigate, onOpenLeadModal }: Props) {
  const [corridorSlug, setCorridorSlug] = useState<string>("all");
  const [category, setCategory] = useState<"all" | "Residential" | "Commercial">("all");
  const [maxBudget, setMaxBudget] = useState<BudgetCap>("any");
  const [facing, setFacing] = useState<"all" | Facing>("all");
  const [cornerOnly, setCornerOnly] = useState(false);

  const matchingPlots = useMemo(() => {
    const budgetLimit = maxBudget === "any" ? Number.POSITIVE_INFINITY : Number(maxBudget);
    const list: Array<{
      projectSlug: string;
      projectName: string;
      corridor: string;
      plotNo: string;
      displayNo: string;
      wing?: string;
      areaSqFt: number;
      faces: Facing;
      road: string;
      category: "Residential" | "Commercial";
      corner: boolean;
      indicativeBaseInr: number;
    }> = [];

    for (const proj of PROJECTS) {
      if (corridorSlug !== "all" && proj.slug !== corridorSlug) continue;
      for (const pl of proj.layout.plots) {
        if (pl.status !== "available") continue;
        const plotCat = pl.category ?? "Residential";
        if (category !== "all" && plotCat !== category) continue;
        if (facing !== "all" && pl.faces !== facing) continue;
        if (cornerOnly && !pl.corner) continue;
        const baseValue = Math.round(pl.areaSqFt * pl.ratePerSqFt);
        if (baseValue > budgetLimit) continue;

        list.push({
          projectSlug: proj.slug,
          projectName: proj.name,
          corridor: proj.corridor,
          plotNo: pl.no,
          displayNo: pl.displayNo ?? pl.no,
          wing: pl.wing,
          areaSqFt: pl.areaSqFt,
          faces: pl.faces,
          road: pl.road,
          category: plotCat,
          corner: Boolean(pl.corner),
          indicativeBaseInr: baseValue,
        });
      }
    }

    return list.sort((a, b) => a.indicativeBaseInr - b.indicativeBaseInr);
  }, [corridorSlug, category, maxBudget, facing, cornerOnly]);

  const hasActiveFilter =
    corridorSlug !== "all" ||
    category !== "all" ||
    maxBudget !== "any" ||
    facing !== "all" ||
    cornerOnly;

  const resetFilters = () => {
    setCorridorSlug("all");
    setCategory("all");
    setMaxBudget("any");
    setFacing("all");
    setCornerOnly(false);
  };

  return (
    <section
      aria-labelledby="plot-finder-heading"
      className="bg-[#F6F5F0] py-12 sm:py-16 dark:bg-[#080C0A]"
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-10">
        <div className="rounded-3xl border border-black/[0.08] bg-white p-6 sm:p-8 dark:border-white/[0.09] dark:bg-[#0F1713]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="inline-flex items-center gap-1.5 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Quick Plot Finder</span>
              </p>
              <h2
                id="plot-finder-heading"
                className="mt-1.5 text-2xl font-semibold tracking-tight text-[#102018] sm:text-3xl dark:text-[#F4F1E7]"
              >
                Find your right Nagpur plot in seconds.
              </h2>
            </div>

            <div className="flex items-center gap-3">
              <div className="rounded-2xl bg-[#F4F3EE] px-4 py-2 text-right dark:bg-[#15211B]">
                <p className="font-mono-tabular text-lg leading-none font-semibold text-[#102018] dark:text-[#E5C77B]">
                  {matchingPlots.length}
                </p>
                <p className="mt-1 text-[11px] font-medium text-[#5A695F] dark:text-[#A3B2A7]">
                  Open Plots
                </p>
              </div>
              {hasActiveFilter ? (
                <button
                  type="button"
                  onClick={resetFilters}
                  className="inline-flex min-h-[42px] items-center gap-1.5 rounded-xl border border-black/10 px-3.5 py-2 text-xs font-medium text-[#4A5950] transition-colors hover:border-[#8C6D2E] dark:border-white/10 dark:text-[#B5C1B8] dark:hover:border-[#D4AF5F]"
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Reset</span>
                </button>
              ) : null}
            </div>
          </div>

          {/* Clean Single-Row Filter Controls */}
          <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <label className="block text-xs font-medium">
              <span className="text-[#516057] dark:text-[#98A89D]">Location</span>
              <select
                value={corridorSlug}
                onChange={(e) => setCorridorSlug(e.target.value)}
                className="mt-1.5 min-h-[44px] w-full rounded-xl border border-black/10 bg-[#F7F6F2] px-3.5 py-2 text-xs font-medium text-[#102018] dark:border-white/10 dark:bg-[#0B120F] dark:text-[#F2EFE7]"
              >
                <option value="all">All 4 Nagpur Layouts</option>
                {PROJECTS.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-xs font-medium">
              <span className="text-[#516057] dark:text-[#98A89D]">Plot Type</span>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as "all" | "Residential" | "Commercial")}
                className="mt-1.5 min-h-[44px] w-full rounded-xl border border-black/10 bg-[#F7F6F2] px-3.5 py-2 text-xs font-medium text-[#102018] dark:border-white/10 dark:bg-[#0B120F] dark:text-[#F2EFE7]"
              >
                <option value="all">Residential &amp; Commercial</option>
                <option value="Residential">Residential Plots</option>
                <option value="Commercial">Commercial / Main Road</option>
              </select>
            </label>

            <label className="block text-xs font-medium">
              <span className="text-[#516057] dark:text-[#98A89D]">Budget*</span>
              <select
                value={maxBudget}
                onChange={(e) => setMaxBudget(e.target.value as BudgetCap)}
                className="mt-1.5 min-h-[44px] w-full rounded-xl border border-black/10 bg-[#F7F6F2] px-3.5 py-2 text-xs font-medium text-[#102018] dark:border-white/10 dark:bg-[#0B120F] dark:text-[#F2EFE7]"
              >
                <option value="any">Any Budget</option>
                <option value="2500000">Under ₹25 Lakh*</option>
                <option value="3500000">Under ₹35 Lakh*</option>
                <option value="5000000">Under ₹50 Lakh*</option>
                <option value="7500000">Under ₹75 Lakh*</option>
              </select>
            </label>

            <label className="block text-xs font-medium">
              <span className="text-[#516057] dark:text-[#98A89D]">Facing Direction</span>
              <select
                value={facing}
                onChange={(e) => setFacing(e.target.value as "all" | Facing)}
                className="mt-1.5 min-h-[44px] w-full rounded-xl border border-black/10 bg-[#F7F6F2] px-3.5 py-2 text-xs font-medium text-[#102018] dark:border-white/10 dark:bg-[#0B120F] dark:text-[#F2EFE7]"
              >
                <option value="all">All Directions</option>
                <option value="E">East Facing</option>
                <option value="N">North Facing</option>
                <option value="W">West Facing</option>
                <option value="S">South Facing</option>
              </select>
            </label>

            <div className="flex flex-col justify-end">
              <label className="flex min-h-[44px] cursor-pointer items-center justify-between gap-2 rounded-xl border border-black/10 bg-[#F7F6F2] px-3.5 py-2 text-xs font-medium text-[#102018] dark:border-white/10 dark:bg-[#0B120F] dark:text-[#F2EFE7]">
                <span className="inline-flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  <span>Corner Plots</span>
                </span>
                <input
                  type="checkbox"
                  checked={cornerOnly}
                  onChange={(e) => setCornerOnly(e.target.checked)}
                  className="h-4 w-4 accent-[#D4AF5F]"
                />
              </label>
            </div>
          </div>

          {/* Airy 1-Tap Matching Plot Cards */}
          {matchingPlots.length > 0 ? (
            <div className="mt-6 grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
              {matchingPlots.slice(0, 4).map((item) => (
                <button
                  key={`${item.projectSlug}-${item.plotNo}`}
                  type="button"
                  onClick={() => {
                    trackEvent({
                      type: "cta_click",
                      page: "/",
                      project: item.projectSlug,
                      plotNumber: item.plotNo,
                      meta: "plot_finder_jump_360",
                    });
                    onNavigate(`/projects/${item.projectSlug}?plot=${encodeURIComponent(item.plotNo)}`);
                  }}
                  className="group flex flex-col justify-between rounded-2xl border border-black/[0.08] bg-[#F8F7F2] p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#0B120F] dark:hover:border-[#D4AF5F]"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
                        <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                        <span className="truncate">{item.projectName}</span>
                      </span>
                      <span className="text-[11px] font-semibold text-[#2E6B42] dark:text-[#7FBB8C]">
                        Open
                      </span>
                    </div>
                    <p className="mt-1.5 text-lg font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
                      Plot {item.displayNo}
                      {item.wing ? ` · ${item.wing}` : ""}
                    </p>
                    <p className="font-mono-tabular mt-1 text-xs text-[#516057] dark:text-[#98A89D]">
                      {item.areaSqFt.toLocaleString("en-IN")} sq.ft · {FACING_LABEL[item.faces].replace("-Facing", "")}
                      {item.corner ? " · Corner" : ""}
                    </p>
                  </div>
                  <div className="mt-4 flex items-center justify-between border-t border-black/[0.08] pt-3 text-xs dark:border-white/10">
                    <span className="font-mono-tabular font-semibold text-[#102018] dark:text-[#E5C77B]">
                      {formatINRCompact(item.indicativeBaseInr)}*
                    </span>
                    <span className="inline-flex items-center gap-1 font-semibold text-[#10281E] group-hover:text-[#8C6D2E] dark:text-[#D5DDD7] dark:group-hover:text-[#E5C77B]">
                      <Compass className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>360° View</span>
                      <ArrowRight className="h-3 w-3" aria-hidden="true" />
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="mt-6 flex flex-col items-start justify-between gap-3 rounded-2xl border border-black/10 bg-[#F8F7F2] p-5 sm:flex-row sm:items-center dark:border-white/10 dark:bg-[#0B120F]">
              <p className="text-sm text-[#4F5D54] dark:text-[#9AA99E]">
                No plots match these exact filters right now. Reset to view all available Nagpur plots.
              </p>
              <button
                type="button"
                onClick={resetFilters}
                className="rounded-xl bg-[#10281E] px-4 py-2.5 text-xs font-semibold text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
              >
                Show All Open Plots
              </button>
            </div>
          )}

          {/* Footer Strip with WhatsApp Shortlist + Pricing Disclaimer */}
          <div className="mt-5 flex flex-col gap-3 border-t border-black/[0.08] pt-4 sm:flex-row sm:items-center sm:justify-between dark:border-white/10">
            <PricingDisclaimer variant="compact" className="max-w-2xl" />
            <button
              type="button"
              onClick={() =>
                onOpenLeadModal({
                  projectSlug: corridorSlug === "all" ? PROJECTS[0].slug : corridorSlug,
                  budgetInr: maxBudget === "any" ? null : Number(maxBudget),
                  interest: "callback",
                })
              }
              className="inline-flex min-h-[44px] shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-[#25D366]/45 bg-[#0c2417] px-4 py-2 text-xs font-semibold text-[#7CF0A6] transition-colors hover:border-[#25D366]"
            >
              <WhatsAppIcon className="h-3.5 w-3.5 text-[#25D366]" />
              <span>Get Shortlist on WhatsApp ({matchingPlots.length})</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
