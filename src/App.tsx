import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { FileCheck2, X } from "lucide-react";
import { getProjectBySlug, PROJECTS } from "./data/projects";
import { setAnalyticsConsent, trackEvent } from "./lib/analytics";
import { resetScrollTopInstant, scrollToSmooth, useSmoothScroll } from "./hooks/useSmoothScroll";
import { SiteFooter, SiteHeader } from "./components/SiteChrome";
import type { LeadSubmissionResult } from "./components/LeadCaptureModal";
import HomePage from "./pages/HomePage";

const ProjectLandingPage = lazy(() => import("./pages/ProjectLandingPage"));
const ThankYouPage = lazy(() => import("./pages/ThankYouPage"));
const LeadCaptureModal = lazy(() => import("./components/LeadCaptureModal"));
const SiteVisitBookingModal = lazy(() => import("./components/SiteVisitBookingModal"));
const CrmAnalyticsDrawer = lazy(() => import("./components/CrmAnalyticsDrawer"));
const PrivacyPolicyPage = lazy(() =>
  import("./pages/LegalPages").then((m) => ({ default: m.PrivacyPolicyPage })),
);
const TermsAndConditionsPage = lazy(() =>
  import("./pages/LegalPages").then((m) => ({ default: m.TermsAndConditionsPage })),
);

type RouteState = {
  pathname: string;
  plotQuery: string | null;
};

function readRoute(): RouteState {
  if (typeof window === "undefined") return { pathname: "/", plotQuery: null };
  const params = new URLSearchParams(window.location.search);
  return {
    pathname: window.location.pathname || "/",
    plotQuery: params.get("plot"),
  };
}

export default function App() {
  const [route, setRoute] = useState<RouteState>(readRoute);
  const [isDark, setIsDark] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const saved = window.localStorage.getItem("realtor-estates-theme");
    return saved ? saved === "dark" : true;
  });

  const [cookieChoice, setCookieChoice] = useState<"pending" | "accepted" | "essential">(() => {
    if (typeof window === "undefined") return "pending";
    const saved = window.localStorage.getItem("realtor-estates-cookie");
    if (saved === "accepted" || saved === "essential") return saved;
    return "pending";
  });

  const [leadModal, setLeadModal] = useState<{
    open: boolean;
    projectSlug: string;
    plotNumber: string;
    budgetInr: number | null;
    loanRequired: boolean;
    interest: "site-visit" | "plot-enquiry" | "callback";
  }>({
    open: false,
    projectSlug: PROJECTS[0].slug,
    plotNumber: "",
    budgetInr: null,
    loanRequired: false,
    interest: "site-visit",
  });

  const [lastConfirmation, setLastConfirmation] = useState<LeadSubmissionResult | null>(null);
  const [advisorDrawerOpen, setAdvisorDrawerOpen] = useState(false);
  const [heatmapOverlay, setHeatmapOverlay] = useState(false);
  const [liveHeatmapPoints, setLiveHeatmapPoints] = useState<Array<{ xPct: number; yPct: number }>>([]);
  const [gmpQuotaExceeded, setGmpQuotaExceeded] = useState(false);

  // Behavior-triggered smart nudge (never an arrival popup; only after 2+ genuine plot/EMI interactions)
  const [smartNudge, setSmartNudge] = useState<{
    visible: boolean;
    projectSlug: string;
    plotNo?: string;
    reason: "plots" | "emi";
  }>({ visible: false, projectSlug: PROJECTS[0].slug, reason: "plots" });

  // Butter-smooth desktop inertial scroll via Lenis (paused while modals/drawers are open)
  useSmoothScroll(leadModal.open || advisorDrawerOpen);

  // Listen for Google Maps Platform Demo Key quota exhaustion events
  useEffect(() => {
    const onQuota = () => setGmpQuotaExceeded(true);
    window.addEventListener("gmp-quota-exceeded", onQuota);
    return () => window.removeEventListener("gmp-quota-exceeded", onQuota);
  }, []);

  // Listen for high-intent buyer engagement (2+ 360° plot views or 2+ EMI adjustments)
  useEffect(() => {
    let plotInspectCount = 0;
    let emiAdjustCount = 0;

    const onEngagement = (ev: Event) => {
      if (window.sessionStorage.getItem("realtor-nudge-dismissed") === "1") return;
      const detail = (ev as CustomEvent<{ kind?: string; projectSlug?: string; plotNo?: string }>).detail;
      if (!detail) return;
      if (detail.kind === "plot_inspect") {
        plotInspectCount++;
        if (plotInspectCount >= 2) {
          setSmartNudge({
            visible: true,
            projectSlug: detail.projectSlug || PROJECTS[0].slug,
            plotNo: detail.plotNo,
            reason: "plots",
          });
        }
      } else if (detail.kind === "emi_adjust") {
        emiAdjustCount++;
        if (emiAdjustCount >= 2) {
          setSmartNudge({
            visible: true,
            projectSlug: detail.projectSlug || PROJECTS[0].slug,
            reason: "emi",
          });
        }
      }
    };

    window.addEventListener("realtor-engagement", onEngagement);
    return () => window.removeEventListener("realtor-engagement", onEngagement);
  }, []);

  // Keep HTML dark class synchronized
  useEffect(() => {
    const root = document.documentElement;
    if (isDark) root.classList.add("dark");
    else root.classList.remove("dark");
    window.localStorage.setItem("realtor-estates-theme", isDark ? "dark" : "light");
  }, [isDark]);

  // Sync cookie consent with analytics tracker
  useEffect(() => {
    setAnalyticsConsent(cookieChoice !== "essential");
  }, [cookieChoice]);

  // Listen to browser back/forward
  useEffect(() => {
    const onPop = () => {
      setRoute(readRoute());
      resetScrollTopInstant();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Track pageviews & sync full dynamic SEO metadata (title, description, canonical, OG, robots)
  useEffect(() => {
    const upsertMeta = (attr: "name" | "property", key: string, content: string) => {
      let tag = document.head.querySelector(`meta[${attr}="${key}"]`);
      if (!tag) {
        tag = document.createElement("meta");
        tag.setAttribute(attr, key);
        document.head.appendChild(tag);
      }
      tag.setAttribute("content", content);
    };
    const upsertCanonical = (href: string) => {
      let link = document.head.querySelector('link[rel="canonical"]');
      if (!link) {
        link = document.createElement("link");
        link.setAttribute("rel", "canonical");
        document.head.appendChild(link);
      }
      link.setAttribute("href", href);
    };

    const origin = window.location.origin;
    const cleanPath = route.pathname.replace(/\/$/, "") || "/";
    const thankYouMatch = route.pathname.match(/^\/projects\/([^/]+)\/thank-you\/?$/);
    const projectMatch = route.pathname.match(/^\/projects\/([^/]+)\/?$/);

    if (cleanPath === "/privacy-policy") {
      const title = "Privacy Policy — Realtor Estates | Property in Nagpur";
      const desc =
        "Read Realtor Estates' Privacy Policy covering GDPR & India DPDP Act 2023 compliance, cookie controls, and zero data resale for Nagpur plot enquiries.";
      const canonical = `${origin}/privacy-policy`;
      document.title = title;
      upsertMeta("name", "description", desc);
      upsertMeta("name", "robots", "index, follow");
      upsertMeta("property", "og:title", title);
      upsertMeta("property", "og:description", desc);
      upsertMeta("property", "og:url", canonical);
      upsertCanonical(canonical);
      trackEvent({ type: "pageview", page: "/privacy-policy" });
    } else if (cleanPath === "/terms-and-conditions") {
      const title = "Terms & Conditions — Realtor Estates | Property in Nagpur";
      const desc =
        "Terms & Conditions, Pricing & Loan Calculator Disclaimers, and NMRDA/MahaRERA due-diligence guidelines for Realtor Estates — Property in Nagpur.";
      const canonical = `${origin}/terms-and-conditions`;
      document.title = title;
      upsertMeta("name", "description", desc);
      upsertMeta("name", "robots", "index, follow");
      upsertMeta("property", "og:title", title);
      upsertMeta("property", "og:description", desc);
      upsertMeta("property", "og:url", canonical);
      upsertCanonical(canonical);
      trackEvent({ type: "pageview", page: "/terms-and-conditions" });
    } else if (thankYouMatch) {
      const proj = getProjectBySlug(thankYouMatch[1]) ?? PROJECTS[0];
      const title = `Confirmed — ${proj.name} | Realtor Estates`;
      const desc = `Your ${proj.name} enquiry is registered with Realtor Estates — Property in Nagpur.`;
      const canonical = `${origin}/projects/${proj.slug}/thank-you`;
      document.title = title;
      upsertMeta("name", "description", desc);
      upsertMeta("name", "robots", "noindex, nofollow");
      upsertMeta("property", "og:title", title);
      upsertMeta("property", "og:description", desc);
      upsertMeta("property", "og:url", canonical);
      upsertCanonical(canonical);
      trackEvent({ type: "pageview", page: route.pathname, project: proj.slug });
    } else if (projectMatch) {
      const proj = getProjectBySlug(projectMatch[1]) ?? PROJECTS[0];
      // Canonicalize legacy slug in address bar cleanly if accessed via client navigation
      if (projectMatch[1] !== proj.slug) {
        window.history.replaceState({}, "", `/projects/${proj.slug}${window.location.search}`);
      }
      const priceHint = proj.commercialRatePerSqFt
        ? `Res. ₹${proj.baseRatePerSqFt.toLocaleString("en-IN")} / Com. ₹${proj.commercialRatePerSqFt.toLocaleString("en-IN")} sq.ft`
        : `From ₹${proj.baseRatePerSqFt.toLocaleString("en-IN")}/sq.ft`;
      const title = `${proj.name} (${proj.khNo}) — 3D Plots | Realtor Estates`;
      const desc = `Explore ${proj.name} in ${proj.location}. ${priceHint}. Rotate individual plots 360° with live measurements, true-north compass & EMI calculator.`;
      const canonical = `${origin}/projects/${proj.slug}`;
      document.title = title;
      upsertMeta("name", "description", desc);
      upsertMeta("name", "robots", "index, follow, max-image-preview:large");
      upsertMeta("property", "og:title", title);
      upsertMeta("property", "og:description", desc);
      upsertMeta("property", "og:url", canonical);
      upsertCanonical(canonical);
      trackEvent({ type: "pageview", page: `/projects/${proj.slug}`, project: proj.slug });
    } else {
      const title = "Realtor Estates — Property in Nagpur | NMRDA Plots";
      const desc =
        "Buy Your Estates, Build Your Legacy in Nagpur. Inspect NMRDA plots in 3D & 360° across Salai Dabha, Kanholi & Hingna — starting ₹1,700/sq.ft with EMI tools.";
      const canonical = `${origin}/`;
      document.title = title;
      upsertMeta("name", "description", desc);
      upsertMeta("name", "robots", "index, follow, max-image-preview:large");
      upsertMeta("property", "og:title", title);
      upsertMeta("property", "og:description", desc);
      upsertMeta("property", "og:url", canonical);
      upsertCanonical(canonical);
      trackEvent({ type: "pageview", page: "/" });
    }
  }, [route.pathname]);

  // Capture anonymized click coordinates for real-time session heatmap
  useEffect(() => {
    const onGlobalClick = (ev: MouseEvent) => {
      const xPct = Math.max(0, Math.min(100, (ev.clientX / Math.max(window.innerWidth, 1)) * 100));
      const yPct = Math.max(0, Math.min(100, (ev.clientY / Math.max(window.innerHeight, 1)) * 100));
      setLiveHeatmapPoints((prev) => [{ xPct, yPct }, ...prev].slice(0, 80));
      trackEvent({
        type: "cta_click",
        page: route.pathname,
        xPct,
        yPct,
      });
    };
    window.addEventListener("click", onGlobalClick, { passive: true });
    return () => window.removeEventListener("click", onGlobalClick);
  }, [route.pathname]);

  const navigate = useCallback((path: string) => {
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState({}, "", path);
    }
    setRoute(readRoute());
    scrollToSmooth(0, 0);
  }, []);

  const openLeadModal = useCallback(
    (opts?: {
      projectSlug?: string;
      plotNumber?: string;
      budgetInr?: number | null;
      loanRequired?: boolean;
      interest?: "site-visit" | "plot-enquiry" | "callback";
    }) => {
      setLeadModal({
        open: true,
        projectSlug: opts?.projectSlug || PROJECTS[0].slug,
        plotNumber: opts?.plotNumber || "",
        budgetInr: opts?.budgetInr ?? null,
        loanRequired: opts?.loanRequired ?? false,
        interest: opts?.interest || "site-visit",
      });
    },
    [],
  );

  const handleCookieChoice = (choice: "accepted" | "essential") => {
    setCookieChoice(choice);
    window.localStorage.setItem("realtor-estates-cookie", choice);
  };

  // Match routes cleanly
  const cleanRoutePath = route.pathname.replace(/\/$/, "") || "/";
  const thankYouMatch = route.pathname.match(/^\/projects\/([^/]+)\/thank-you\/?$/);
  const projectMatch = route.pathname.match(/^\/projects\/([^/]+)\/?$/);

  let activeViewKey = "home";
  let activeContent = (
    <HomePage onNavigate={navigate} onOpenLeadModal={openLeadModal} />
  );

  if (cleanRoutePath === "/privacy-policy") {
    activeViewKey = "privacy-policy";
    activeContent = <PrivacyPolicyPage onNavigate={navigate} />;
  } else if (cleanRoutePath === "/terms-and-conditions") {
    activeViewKey = "terms-and-conditions";
    activeContent = <TermsAndConditionsPage onNavigate={navigate} />;
  } else if (thankYouMatch) {
    const proj = getProjectBySlug(thankYouMatch[1]) ?? PROJECTS[0];
    activeViewKey = `thank-you-${proj.slug}`;
    activeContent = (
      <ThankYouPage
        project={proj}
        confirmation={lastConfirmation}
        onNavigate={navigate}
      />
    );
  } else if (projectMatch) {
    const proj = getProjectBySlug(projectMatch[1]) ?? PROJECTS[0];
    activeViewKey = `project-${proj.slug}`;
    activeContent = (
      <ProjectLandingPage
        project={proj}
        initialPlotNo={route.plotQuery}
        onNavigate={navigate}
        onOpenLeadModal={openLeadModal}
      />
    );
  }

  return (
    <MotionConfig reducedMotion="user">
    <div className="min-h-screen bg-[#F6F5F0] text-[#111915] transition-colors duration-200 dark:bg-[#080C0A] dark:text-[#F2EFE7]">
      {gmpQuotaExceeded ? (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-2.5 text-xs md:text-sm text-center sticky top-0 z-50 shadow-sm">
          <span>
            Google Maps Platform quota reached. If you are the app owner, visit{" "}
            <a
              href="https://developers.google.com/maps/ai/ai-studio?utm_campaign=gmp_mcp_codeassist_v1_aistudio#quota_exceeded_errors"
              target="_blank"
              rel="noopener noreferrer"
              className="underline font-semibold text-amber-950 hover:text-amber-800"
            >
              maps developer site
            </a>{" "}
            for instructions to update your account.
          </span>
        </div>
      ) : null}

      <SiteHeader
        currentPath={route.pathname}
        onNavigate={navigate}
        onOpenVisitModal={() =>
          openLeadModal({
            projectSlug: projectMatch ? projectMatch[1] : PROJECTS[0].slug,
            interest: "site-visit",
          })
        }
      />

      <main id="main">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeViewKey}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
          >
            <Suspense
              fallback={
                <div className="flex min-h-[60svh] items-center justify-center text-xs tracking-wider text-[#6E7F74] dark:text-[#98A89D]">
                  Loading Estate Experience…
                </div>
              }
            >
              {activeContent}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </main>

      <SiteFooter
        isDark={isDark}
        onToggleTheme={() => setIsDark((d) => !d)}
        onNavigate={navigate}
        onOpenAdvisorDrawer={() => setAdvisorDrawerOpen(true)}
        onOpenVisitModal={() =>
          openLeadModal({
            projectSlug: projectMatch ? projectMatch[1] : PROJECTS[0].slug,
            interest: "site-visit",
          })
        }
        onOpenWhatsAppModal={() =>
          openLeadModal({
            projectSlug: projectMatch ? projectMatch[1] : PROJECTS[0].slug,
            interest: "callback",
          })
        }
        cookieChoice={cookieChoice}
        onCookieChoice={handleCookieChoice}
      />

      {leadModal.open ? (
        <Suspense fallback={null}>
          {leadModal.interest === "site-visit" ? (
            <SiteVisitBookingModal
              open={leadModal.open}
              defaultProjectSlug={leadModal.projectSlug}
              defaultPlotNumber={leadModal.plotNumber}
              onClose={() => setLeadModal((m) => ({ ...m, open: false }))}
              onSuccess={(result) => {
                setLeadModal((m) => ({ ...m, open: false }));
                setSmartNudge((s) => ({ ...s, visible: false }));
                window.sessionStorage.setItem("realtor-nudge-dismissed", "1");
                setLastConfirmation(result);
                navigate(`/projects/${result.projectSlug}/thank-you`);
              }}
            />
          ) : (
            <LeadCaptureModal
              open={leadModal.open}
              defaultProjectSlug={leadModal.projectSlug}
              defaultPlotNumber={leadModal.plotNumber}
              defaultBudgetInr={leadModal.budgetInr}
              defaultLoanRequired={leadModal.loanRequired}
              interest={leadModal.interest}
              onClose={() => setLeadModal((m) => ({ ...m, open: false }))}
              onSuccess={(result) => {
                setLeadModal((m) => ({ ...m, open: false }));
                setSmartNudge((s) => ({ ...s, visible: false }));
                window.sessionStorage.setItem("realtor-nudge-dismissed", "1");
                setLastConfirmation(result);
                navigate(`/projects/${result.projectSlug}/thank-you`);
              }}
            />
          )}
        </Suspense>
      ) : null}

      {/* Behavior-Triggered Smart Nudge (Only after genuine 360° plot or EMI engagement; never an arrival popup) */}
      {smartNudge.visible && !leadModal.open && !thankYouMatch ? (
        <aside
          aria-label="Written cost sheet offer"
          className="fixed right-3 bottom-20 left-3 z-30 mx-auto max-w-md rounded-xl border border-[#D4AF5F]/45 bg-[#0C1712]/96 p-3.5 text-[#F5F2E9] shadow-xl sm:right-5 sm:bottom-5 sm:left-auto"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2.5">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#D4AF5F]/15 text-[#E5C77B]">
                <FileCheck2 className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <p className="text-xs font-semibold text-[#F7F4EC]">
                  {smartNudge.reason === "emi"
                    ? "Want a dated Written Cost Sheet & Bank EMI Brief?"
                    : "Shortlisting Nagpur plots? Get Written Confirmation."}
                </p>
                <p className="mt-0.5 text-[11px] leading-snug text-[#B8C6BC]">
                  Receive verified availability + our 14-Point Maharashtra Plot Buyer’s Guide in 5 seconds.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setSmartNudge((s) => ({ ...s, visible: false }));
                    window.sessionStorage.setItem("realtor-nudge-dismissed", "1");
                    openLeadModal({
                      projectSlug: smartNudge.projectSlug,
                      plotNumber: smartNudge.plotNo,
                      interest: "plot-enquiry",
                    });
                  }}
                  className="mt-2 inline-flex min-h-[34px] items-center gap-1.5 rounded-lg bg-[#D4AF5F] px-3 py-1.5 text-[11px] font-semibold text-[#080C0A] hover:bg-[#e5c77b]"
                >
                  <span>Unlock Cost Sheet &amp; Buyer’s Guide</span>
                </button>
              </div>
            </div>
            <button
              type="button"
              aria-label="Dismiss offer"
              onClick={() => {
                setSmartNudge((s) => ({ ...s, visible: false }));
                window.sessionStorage.setItem("realtor-nudge-dismissed", "1");
              }}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[#9EB0A3] hover:bg-white/10 hover:text-white"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </aside>
      ) : null}

      {advisorDrawerOpen ? (
        <Suspense fallback={null}>
          <CrmAnalyticsDrawer
            open={advisorDrawerOpen}
            heatmapOverlayEnabled={heatmapOverlay}
            onToggleHeatmapOverlay={() => setHeatmapOverlay((h) => !h)}
            onClose={() => setAdvisorDrawerOpen(false)}
          />
        </Suspense>
      ) : null}

      {/* Optional Live Session Click Heatmap Overlay (toggled exclusively by Advisor Drawer) */}
      {heatmapOverlay ? (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-40 overflow-hidden"
        >
          {liveHeatmapPoints.map((pt, i) => (
            <span
              key={`${pt.xPct}-${pt.yPct}-${i}`}
              style={{ left: `${pt.xPct}%`, top: `${pt.yPct}%` }}
              className="absolute -ml-6 -mt-6 h-12 w-12 rounded-full bg-[#D4AF5F]/30 blur-md"
            />
          ))}
        </div>
      ) : null}
    </div>
    </MotionConfig>
  );
}
