import { useState } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  Building2,
  Calculator,
  CalendarCheck,
  Compass,
  FileText,
  Home,
  MapPin,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { PROJECTS, summarizeLayout } from "../data/projects";
import { trackEvent } from "../lib/analytics";
import { scrollToSmooth } from "../hooks/useSmoothScroll";
import { WhatsAppIcon } from "./PricingDisclaimer";

const BRAND_LOGO_SRC = "/website/assets/aistudio/Realtor_Estates_8f1cca01.jpeg";

type HeaderProps = {
  onNavigate: (path: string) => void;
  onOpenVisitModal: () => void;
  currentPath: string;
};

export function SiteHeader({ onNavigate, onOpenVisitModal, currentPath }: HeaderProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  const goSection = (hash: string) => {
    setMobileOpen(false);
    if (currentPath !== "/") {
      onNavigate("/");
      setTimeout(() => {
        document.getElementById(hash)?.scrollIntoView({ behavior: "smooth" });
      }, 150);
    } else {
      document.getElementById(hash)?.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-black/[0.08] bg-[#F6F5F0]/90 backdrop-blur-xl dark:border-white/[0.08] dark:bg-[#080C0A]/90">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-[#D4AF5F] focus:px-4 focus:py-2 focus:text-xs focus:font-semibold focus:text-[#080C0A]"
        >
          Skip to main content
        </a>
        {/* Clean 3-Zone Header: [Uncropped Brand Crest + Tight Wordmark] — [4 clean single-line links, NO Buyer Guide] — [1 Primary CTA] */}
        <div className="mx-auto flex h-[62px] max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 md:h-[72px] lg:gap-8 lg:px-10">
          {/* Zone 1: Full Uncropped Brand Crest Symbol + Tightly Nested Smaller Tagline */}
          <a
            href="/"
            onClick={(e) => {
              e.preventDefault();
              setMobileOpen(false);
              onNavigate("/");
            }}
            aria-label="Realtor Estates — Property in Nagpur Home"
            className="group flex min-h-[44px] shrink-0 items-center gap-2.5 whitespace-nowrap sm:gap-3"
          >
            <span className="relative h-10 w-10 shrink-0 overflow-hidden rounded-xl bg-[#080C0A] ring-1 ring-[#D4AF5F]/40 sm:h-11 sm:w-11 dark:bg-[#080C0A] dark:ring-[#D4AF5F]/30">
              <img
                src={BRAND_LOGO_SRC}
                alt="Realtor Estates golden architectural crest symbol"
                width={44}
                height={44}
                decoding="async"
                className="pointer-events-none absolute top-[-9%] left-1/2 w-[178%] max-w-none -translate-x-1/2 select-none dark:mix-blend-screen"
              />
            </span>
            <span className="flex flex-col items-start justify-center">
              <span className="font-editorial text-[21px] leading-[0.95] font-semibold tracking-tight text-[#102018] sm:text-2xl dark:text-[#F4F1E7]">
                Realtor Estates
              </span>
              <span className="mt-[2px] text-[8.5px] leading-none font-semibold tracking-[0.19em] text-[#8C6D2E] uppercase sm:text-[9px] dark:text-[#D4AF5F]">
                Property in Nagpur
              </span>
            </span>
          </a>

          {/* Zone 2: Clean single-line navigation links with globally recognized real-estate icons */}
          <nav
            aria-label="Primary navigation"
            className="hidden items-center gap-7 text-[13px] font-medium text-[#46544C] md:flex lg:gap-9 dark:text-[#B5C1B8]"
          >
            <a
              href="/"
              onClick={(e) => {
                e.preventDefault();
                onNavigate("/");
              }}
              className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap transition-colors hover:text-[#102018] dark:hover:text-[#F4F1E7] ${
                currentPath === "/" ? "font-semibold text-[#102018] dark:text-[#E5C77B]" : ""
              }`}
            >
              <Home className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
              <span>Home</span>
            </a>
            <a
              href="#estates"
              onClick={(e) => {
                e.preventDefault();
                goSection("estates");
              }}
              className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap transition-colors hover:text-[#102018] dark:hover:text-[#F4F1E7]"
            >
              <Building2 className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
              <span>Nagpur Plots</span>
            </a>
            <a
              href="#nagpur-satellite-map"
              onClick={(e) => {
                e.preventDefault();
                goSection("nagpur-satellite-map");
              }}
              className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap transition-colors hover:text-[#102018] dark:hover:text-[#F4F1E7]"
            >
              <Compass className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
              <span>Satellite &amp; 3D</span>
            </a>
            <a
              href="#estimator"
              onClick={(e) => {
                e.preventDefault();
                goSection("estimator");
              }}
              className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap transition-colors hover:text-[#102018] dark:hover:text-[#F4F1E7]"
            >
              <Calculator className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
              <span>EMI Planner</span>
            </a>
          </nav>

          {/* Zone 3: 1 Primary Action + Mobile Menu Trigger */}
          <div className="flex shrink-0 items-center gap-2.5">
            <button
              type="button"
              onClick={() => {
                trackEvent({ type: "cta_click", page: currentPath, meta: "header_book_visit" });
                onOpenVisitModal();
              }}
              className="hidden min-h-[40px] shrink-0 items-center gap-2 whitespace-nowrap rounded-xl bg-[#10281E] px-4 py-2 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193d2e] sm:inline-flex dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
            >
              <CalendarCheck className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Book Site Visit</span>
            </button>

            <button
              type="button"
              onClick={() => setMobileOpen((o) => !o)}
              aria-label={mobileOpen ? "Close menu" : "Open menu"}
              aria-expanded={mobileOpen}
              className="flex h-11 w-11 items-center justify-center rounded-xl border border-black/15 text-[#102018] md:hidden dark:border-white/15 dark:text-[#F4F1E7]"
            >
              {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Drawer — Prominent Direct Redirects to All Projects & Legal Pages */}
        {mobileOpen ? (
          <div className="max-h-[calc(100svh-60px)] overflow-y-auto border-t border-black/10 bg-[#F6F5F0] px-4 py-4 md:hidden dark:border-white/10 dark:bg-[#0B120F]">
            <nav aria-label="Mobile navigation" className="flex flex-col gap-1 text-sm font-medium text-[#16261E] dark:text-[#E4E1D6]">
              <button
                type="button"
                onClick={() => {
                  setMobileOpen(false);
                  onNavigate("/");
                }}
                className="flex min-h-[44px] items-center gap-3 rounded-lg px-2 text-left hover:bg-black/5 dark:hover:bg-white/5"
              >
                <Home className="h-4 w-4 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                <span>Home</span>
              </button>
              <button
                type="button"
                onClick={() => goSection("estates")}
                className="flex min-h-[44px] items-center gap-3 rounded-lg px-2 text-left hover:bg-black/5 dark:hover:bg-white/5"
              >
                <Building2 className="h-4 w-4 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                <span>Nagpur NMRDA Layouts</span>
              </button>
              <button
                type="button"
                onClick={() => goSection("journey")}
                className="flex min-h-[44px] items-center gap-3 rounded-lg px-2 text-left hover:bg-black/5 dark:hover:bg-white/5"
              >
                <Compass className="h-4 w-4 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                <span>3D Virtual Tour</span>
              </button>
              <button
                type="button"
                onClick={() => goSection("estimator")}
                className="flex min-h-[44px] items-center gap-3 rounded-lg px-2 text-left hover:bg-black/5 dark:hover:bg-white/5"
              >
                <Calculator className="h-4 w-4 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                <span>Plot Budget &amp; Loan EMI</span>
              </button>

              <div className="my-2 border-t border-black/10 pt-2 dark:border-white/10">
                <p className="px-2 pb-1 text-[10px] font-semibold tracking-wider text-[#7A602B] uppercase dark:text-[#D4AF5F]">
                  Direct Project Pages
                </p>
                {PROJECTS.map((project) => (
                  <a
                    key={project.slug}
                    href={`/projects/${project.slug}`}
                    onClick={(e) => {
                      e.preventDefault();
                      setMobileOpen(false);
                      onNavigate(`/projects/${project.slug}`);
                    }}
                    className="flex min-h-[44px] items-center gap-2.5 rounded-lg px-2 text-xs text-[#34443B] hover:bg-black/5 dark:text-[#C5D1C8] dark:hover:bg-white/5"
                  >
                    <MapPin className="h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                    <span>{project.name}</span>
                  </a>
                ))}
              </div>

              <div className="mt-1 flex items-center gap-3 border-t border-black/10 px-2 pt-3 text-xs text-[#54635A] dark:border-white/10 dark:text-[#98A89D]">
                <a
                  href="/privacy-policy"
                  onClick={(e) => {
                    e.preventDefault();
                    setMobileOpen(false);
                    onNavigate("/privacy-policy");
                  }}
                  className="py-1 hover:underline"
                >
                  Privacy Policy
                </a>
                <span aria-hidden="true">·</span>
                <a
                  href="/terms-and-conditions"
                  onClick={(e) => {
                    e.preventDefault();
                    setMobileOpen(false);
                    onNavigate("/terms-and-conditions");
                  }}
                  className="py-1 hover:underline"
                >
                  Terms &amp; Conditions
                </a>
              </div>

              <button
                type="button"
                onClick={() => {
                  setMobileOpen(false);
                  onOpenVisitModal();
                }}
                className="mt-3 flex min-h-[48px] w-full items-center justify-center gap-2 rounded-lg bg-[#10281E] px-4 py-3 text-center text-xs font-semibold text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
              >
                <CalendarCheck className="h-4 w-4" />
                <span>Book Free Site Visit</span>
              </button>
            </nav>
          </div>
        ) : null}
      </header>

      {/* Mobile-First Thumb-Zone Bottom Navigation Bar (Globally Recognized Real-Estate & Messaging Symbols) */}
      <nav
        aria-label="Mobile quick navigation"
        className="fixed inset-x-0 bottom-0 z-40 grid h-[58px] grid-cols-4 border-t border-black/10 bg-[#F6F5F0]/95 backdrop-blur-md md:hidden dark:border-[#D4AF5F]/20 dark:bg-[#080C0A]/95"
      >
        <button
          type="button"
          onClick={() => onNavigate("/")}
          className={`flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium ${
            currentPath === "/"
              ? "text-[#8C6D2E] dark:text-[#E5C77B]"
              : "text-[#4D5D54] dark:text-[#A3B2A7]"
          }`}
        >
          <Home className="h-4 w-4" aria-hidden="true" />
          <span>Home</span>
        </button>

        <button
          type="button"
          onClick={() => goSection("estates")}
          className={`flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium ${
            currentPath.startsWith("/projects/")
              ? "text-[#8C6D2E] dark:text-[#E5C77B]"
              : "text-[#4D5D54] dark:text-[#A3B2A7]"
          }`}
        >
          <Building2 className="h-4 w-4" aria-hidden="true" />
          <span>Layouts</span>
        </button>

        <button
          type="button"
          onClick={() => goSection("estimator")}
          className="flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium text-[#4D5D54] dark:text-[#A3B2A7]"
        >
          <Calculator className="h-4 w-4" aria-hidden="true" />
          <span>EMI</span>
        </button>

        <button
          type="button"
          onClick={() => {
            trackEvent({ type: "cta_click", page: currentPath, meta: "mobile_bottom_whatsapp_enquire" });
            onOpenVisitModal();
          }}
          className="flex flex-col items-center justify-center gap-0.5 text-[10px] font-semibold text-[#1E7E45] dark:text-[#4CD97B]"
        >
          <WhatsAppIcon className="h-4 w-4" />
          <span>WhatsApp</span>
        </button>
      </nav>
    </>
  );
}

type FooterProps = {
  isDark: boolean;
  onToggleTheme: () => void;
  onNavigate: (path: string) => void;
  onOpenAdvisorDrawer: () => void;
  onOpenVisitModal: () => void;
  onOpenWhatsAppModal: () => void;
  cookieChoice: "pending" | "accepted" | "essential";
  onCookieChoice: (choice: "accepted" | "essential") => void;
};

export function SiteFooter({
  isDark,
  onToggleTheme,
  onNavigate,
  onOpenAdvisorDrawer,
  onOpenVisitModal,
  onOpenWhatsAppModal,
  cookieChoice,
  onCookieChoice,
}: FooterProps) {
  const [hoveredEstateSlug, setHoveredEstateSlug] = useState<string>(PROJECTS[0].slug);
  const previewEstate =
    PROJECTS.find((p) => p.slug === hoveredEstateSlug) ?? PROJECTS[0];
  const previewStats = summarizeLayout(previewEstate.layout);

  const goHomeSection = (sectionId: string) => {
    if (window.location.pathname !== "/") {
      onNavigate("/");
      setTimeout(() => {
        scrollToSmooth(`#${sectionId}`);
      }, 180);
    } else {
      scrollToSmooth(`#${sectionId}`);
    }
  };

  return (
    <footer className="border-t border-black/[0.08] bg-[#ECEAE1] pb-20 text-[#17231C] md:pb-0 dark:border-white/[0.08] dark:bg-[#060A08] dark:text-[#E5E2D7]">
      {/* Apple HIG Interactive Top Utility & Quick-Booking Strip */}
      <div className="border-b border-black/[0.08] bg-[#F4F2EA]/80 dark:border-white/[0.08] dark:bg-[#0B120F]/90">
        <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-4 px-4 py-6 sm:flex-row sm:items-center sm:px-6 lg:px-10">
          <div>
            <p className="text-xs font-medium text-[#8C6D2E] dark:text-[#D4AF5F]">
              Realtor Estates · Nagpur Advisory Desk
            </p>
            <p className="mt-0.5 text-base font-semibold tracking-tight text-[#102018] sm:text-lg dark:text-white">
              Ready to inspect your shortlisted Nagpur plot in person?
            </p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2.5 sm:w-auto">
            <button
              type="button"
              onClick={onOpenVisitModal}
              className="inline-flex min-h-[42px] flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#10281E] px-4 py-2 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] sm:flex-none dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
            >
              <CalendarCheck className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>Book Free Site Visit</span>
            </button>
            <button
              type="button"
              onClick={onOpenWhatsAppModal}
              className="inline-flex min-h-[42px] flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-[#25D366]/45 bg-white px-4 py-2 text-xs font-semibold text-[#166534] transition-colors hover:border-[#25D366] sm:flex-none dark:bg-[#0c2417] dark:text-[#7CF0A6]"
            >
              <WhatsAppIcon className="h-3.5 w-3.5 shrink-0 text-[#25D366]" />
              <span>WhatsApp Desk</span>
            </button>
            <button
              type="button"
              onClick={() => scrollToSmooth(0)}
              aria-label="Scroll back to top"
              className="inline-flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border border-black/10 bg-white text-[#102018] transition-all hover:-translate-y-0.5 hover:border-[#8C6D2E] dark:border-white/12 dark:bg-[#101814] dark:text-[#E5C77B] dark:hover:border-[#D4AF5F]"
            >
              <ArrowUp className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {/* Main Apple-Structured Directory */}
      <div className="mx-auto max-w-7xl px-4 pt-12 pb-8 sm:px-6 lg:px-10 lg:pt-14">
        <div className="grid grid-cols-1 gap-10 border-b border-black/[0.08] pb-10 md:grid-cols-12 dark:border-white/[0.08]">
          {/* Brand Identity */}
          <div className="md:col-span-4">
            <a
              href="/"
              onClick={(e) => {
                e.preventDefault();
                onNavigate("/");
              }}
              className="inline-flex items-center gap-3.5 text-left"
            >
              <div className="overflow-hidden rounded-2xl bg-[#080C0A] p-1.5 ring-1 ring-[#D4AF5F]/30 dark:bg-transparent dark:p-0 dark:ring-0">
                <img
                  src={BRAND_LOGO_SRC}
                  alt="Realtor Estates — Property in Nagpur official emblem"
                  width={68}
                  height={68}
                  loading="lazy"
                  decoding="async"
                  className="h-[68px] w-[68px] object-contain dark:mix-blend-screen"
                />
              </div>
              <div>
                <p className="font-display text-2xl leading-[0.95] font-semibold tracking-tight text-[#102018] sm:text-3xl dark:text-[#F4F1E7]">
                  Realtor Estates
                </p>
                <p className="mt-1 text-[9.5px] leading-none font-semibold tracking-[0.2em] text-[#7E632B] uppercase dark:text-[#D4AF5F]">
                  Property in Nagpur
                </p>
              </div>
            </a>
            <p className="mt-4 max-w-sm text-xs leading-relaxed text-[#4F5D54] dark:text-[#98A89D]">
              NMRDA-sanctioned residential &amp; commercial plots in Nagpur presented with
              interactive 3D layouts, 360° parcel inspection, and transparent 7/12 due-diligence.
            </p>
          </div>

          {/* Interactive Nagpur Estates Directory — Property Names Only (No Kh.No.) */}
          <div className="md:col-span-4">
            <p className="text-[11px] font-semibold tracking-wide text-[#6A7970] uppercase dark:text-[#84958A]">
              Nagpur Estates
            </p>
            <ul className="mt-3 space-y-1.5">
              {PROJECTS.map((project) => {
                const isPreviewed = hoveredEstateSlug === project.slug;
                return (
                  <li key={project.slug}>
                    <a
                      href={`/projects/${project.slug}`}
                      onMouseEnter={() => setHoveredEstateSlug(project.slug)}
                      onFocus={() => setHoveredEstateSlug(project.slug)}
                      onClick={(e) => {
                        e.preventDefault();
                        onNavigate(`/projects/${project.slug}`);
                      }}
                      className={`group flex items-center justify-between rounded-xl px-3 py-2 text-sm transition-all ${
                        isPreviewed
                          ? "bg-white text-[#102018] shadow-xs ring-1 ring-black/[0.06] dark:bg-[#111B16] dark:text-[#F4F1E7] dark:ring-white/10"
                          : "text-[#46554C] hover:bg-white/60 hover:text-[#102018] dark:text-[#9AA99E] dark:hover:bg-[#101814]"
                      }`}
                    >
                      <span className="inline-flex items-center gap-2 font-medium">
                        <MapPin
                          className="h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]"
                          aria-hidden="true"
                        />
                        <span>{project.name}</span>
                      </span>
                      <ArrowUpRight
                        className={`h-3.5 w-3.5 shrink-0 transition-transform duration-200 ${
                          isPreviewed
                            ? "translate-x-0.5 -translate-y-0.5 text-[#8C6D2E] dark:text-[#D4AF5F]"
                            : "text-[#8A9890]"
                        }`}
                        aria-hidden="true"
                      />
                    </a>
                  </li>
                );
              })}
            </ul>
            {/* Quiet Interactive Live Snapshot Strip */}
            <div className="mt-2.5 flex items-center justify-between rounded-xl border border-black/[0.07] bg-white/70 px-3 py-2 text-[11px] text-[#526158] dark:border-white/[0.08] dark:bg-[#0D1511] dark:text-[#98A89D]">
              <span className="truncate font-medium">{previewEstate.corridor}</span>
              <span className="font-mono-tabular ml-2 shrink-0 font-semibold text-[#2E6B42] dark:text-[#7FBB8C]">
                {previewStats.available} Open
              </span>
            </div>
          </div>

          {/* Quick Jump & Legal Column */}
          <div className="md:col-span-2">
            <p className="text-[11px] font-semibold tracking-wide text-[#6A7970] uppercase dark:text-[#84958A]">
              Explore &amp; Legal
            </p>
            <ul className="mt-3 space-y-2 text-xs font-medium text-[#46554C] dark:text-[#9AA99E]">
              <li>
                <button
                  type="button"
                  onClick={() => goHomeSection("nagpur-satellite-map")}
                  className="inline-flex items-center gap-1.5 py-0.5 transition-colors hover:text-[#102018] dark:hover:text-[#E5C77B]"
                >
                  <Compass className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  <span>3D Satellite Map</span>
                </button>
              </li>
              <li>
                <button
                  type="button"
                  onClick={() => goHomeSection("estimator")}
                  className="inline-flex items-center gap-1.5 py-0.5 transition-colors hover:text-[#102018] dark:hover:text-[#E5C77B]"
                >
                  <Calculator className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  <span>EMI Calculator</span>
                </button>
              </li>
              <li>
                <a
                  href="/privacy-policy"
                  onClick={(e) => {
                    e.preventDefault();
                    onNavigate("/privacy-policy");
                  }}
                  className="inline-flex items-center gap-1.5 py-0.5 transition-colors hover:text-[#102018] hover:underline dark:hover:text-[#E5C77B]"
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  <span>Privacy Policy</span>
                </a>
              </li>
              <li>
                <a
                  href="/terms-and-conditions"
                  onClick={(e) => {
                    e.preventDefault();
                    onNavigate("/terms-and-conditions");
                  }}
                  className="inline-flex items-center gap-1.5 py-0.5 transition-colors hover:text-[#102018] hover:underline dark:hover:text-[#E5C77B]"
                >
                  <FileText className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                  <span>Terms &amp; Conditions</span>
                </a>
              </li>
            </ul>
          </div>

          {/* Apple macOS/iOS Style Appearance & Advisor Workspace Column */}
          <div className="flex flex-col gap-3 md:col-span-2 md:items-end">
            <p className="text-[11px] font-semibold tracking-wide text-[#6A7970] uppercase dark:text-[#84958A]">
              Appearance
            </p>
            {/* Segmented Light / Dark Control */}
            <div
              role="group"
              aria-label="Theme appearance"
              className="inline-flex rounded-xl border border-black/10 bg-white p-1 dark:border-white/12 dark:bg-[#101814]"
            >
              <button
                type="button"
                onClick={() => {
                  if (isDark) onToggleTheme();
                }}
                aria-pressed={!isDark}
                className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-semibold transition-all ${
                  !isDark
                    ? "bg-[#10281E] text-[#F6F5F0] shadow-xs"
                    : "text-[#8E9F93] hover:text-white"
                }`}
              >
                <Sun className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Light</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!isDark) onToggleTheme();
                }}
                aria-pressed={isDark}
                className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-semibold transition-all ${
                  isDark
                    ? "bg-[#D4AF5F] text-[#080C0A] shadow-xs"
                    : "text-[#526158] hover:text-[#102018]"
                }`}
              >
                <Moon className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Dark</span>
              </button>
            </div>

            <button
              type="button"
              onClick={onOpenAdvisorDrawer}
              className="inline-flex min-h-[38px] items-center gap-1.5 whitespace-nowrap rounded-xl border border-black/10 bg-white/80 px-3.5 py-1.5 text-xs font-medium text-[#46544C] transition-colors hover:border-[#8C6D2E] hover:text-[#102018] dark:border-white/12 dark:bg-[#101814] dark:text-[#A8B7AC] dark:hover:border-[#D4AF5F] dark:hover:text-[#E5C77B]"
            >
              <span>Advisor CRM</span>
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* RERA, Pricing & Due-Diligence Compliance Disclaimer */}
        <div className="pt-6 text-[11px] leading-relaxed text-[#5C6A61] dark:text-[#86968B]">
          <p>
            <strong className="font-semibold text-[#17231C] dark:text-[#C9D3CB]">
              Compliance, Pricing &amp; Due-Diligence Disclaimer:
            </strong>{" "}
            Interactive 3D layouts, 360-degree plot renders, directional indicators, corridor maps,
            displayed per-sq.ft rates, and financial EMI estimates are illustrative planning aids
            prepared from reference sheets and do not constitute an official sanctioned layout,
            binding price quote, live inventory register, or guarantee of bank loan approval or
            returns. Prior to any financial transaction, prospective buyers are advised to obtain a
            dated written cost sheet and independently verify original title deeds, current 7/12
            (Satbara) extracts, NMRDA/Town Planning layout sanctions, NA orders, and applicable
            MahaRERA disclosures with competent Maharashtra authorities and an independent property
            advocate.
          </p>
        </div>

        {/* GDPR Cookie Consent Notice */}
        <div className="mt-5 flex flex-col items-start justify-between gap-3 border-t border-black/[0.08] pt-4 text-xs text-[#54635A] sm:flex-row sm:items-center dark:border-white/[0.08] dark:text-[#8F9F94]">
          <p className="leading-snug">
            GDPR &amp; Privacy Notice: We use essential cookies and anonymized telemetry solely for
            theme and enquiry preferences — never sold to third parties. Read our{" "}
            <a
              href="/privacy-policy"
              onClick={(e) => {
                e.preventDefault();
                onNavigate("/privacy-policy");
              }}
              className="font-medium text-[#102018] underline underline-offset-2 dark:text-[#E5C77B]"
            >
              Privacy Policy
            </a>{" "}
            &amp;{" "}
            <a
              href="/terms-and-conditions"
              onClick={(e) => {
                e.preventDefault();
                onNavigate("/terms-and-conditions");
              }}
              className="font-medium text-[#102018] underline underline-offset-2 dark:text-[#E5C77B]"
            >
              Terms &amp; Conditions
            </a>
            .
          </p>
          <div className="flex shrink-0 items-center gap-2">
            {cookieChoice === "pending" ? (
              <>
                <button
                  type="button"
                  onClick={() => onCookieChoice("essential")}
                  className="min-h-[34px] whitespace-nowrap rounded-lg border border-black/15 px-3 py-1 text-[11px] font-medium hover:border-black/40 dark:border-white/15 dark:hover:border-white/40"
                >
                  Essential Only
                </button>
                <button
                  type="button"
                  onClick={() => onCookieChoice("accepted")}
                  className="min-h-[34px] whitespace-nowrap rounded-lg bg-[#10281E] px-3 py-1 text-[11px] font-semibold text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                >
                  Accept Preferences
                </button>
              </>
            ) : (
              <span className="whitespace-nowrap text-[11px] font-medium text-[#7E632B] dark:text-[#D4AF5F]">
                Cookie preference saved ({cookieChoice === "accepted" ? "Full" : "Essential only"})
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Extreme Bottom Highlighted Copyright Bar — Placed AFTER All Disclaimers */}
      <div className="border-t border-black/10 bg-[#E3DFD3] dark:border-[#D4AF5F]/20 dark:bg-[#0B120F]">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 px-4 py-4 text-center sm:flex-row sm:px-6 sm:text-left lg:px-10">
          <p className="text-xs font-semibold tracking-tight text-[#102018] dark:text-[#F4F1E7]">
            © 2026 Realtor Estates. All rights reserved.
          </p>
          <p className="text-[11px] font-medium text-[#526158] dark:text-[#9AA99E]">
            Nagpur, Maharashtra, India · Buy Your Estates, Build Your Legacy
          </p>
        </div>
      </div>
    </footer>
  );
}
