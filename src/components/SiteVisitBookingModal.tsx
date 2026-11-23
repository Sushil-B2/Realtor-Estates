import { useEffect, useMemo, useState, type FormEvent } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Calendar,
  CalendarCheck,
  Car,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Compass,
  Home,
  Landmark,
  Loader2,
  MapPin,
  ShieldCheck,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { PROJECTS, summarizeLayout } from "../data/projects";
import { formatINR } from "../lib/finance";
import { trackEvent } from "../lib/analytics";
import type { LeadSubmissionResult } from "./LeadCaptureModal";

const EASE_APPLE = [0.16, 1, 0.3, 1] as const;

type Props = {
  open: boolean;
  defaultProjectSlug?: string;
  defaultPlotNumber?: string;
  onClose: () => void;
  onSuccess: (result: LeadSubmissionResult) => void;
};

type BuyingGoal = {
  id: string;
  label: string;
  sub: string;
  persona: string;
  icon: "home" | "invest" | "commercial" | "nri";
};

const BUYING_GOALS: BuyingGoal[] = [
  {
    id: "home",
    label: "Build Family Home",
    sub: "Peaceful NMRDA residential plot for construction",
    persona: "End-User Family",
    icon: "home",
  },
  {
    id: "invest",
    label: "Long-Term Investment",
    sub: "High-growth Hingna, MIHAN & Wardha Rd belt",
    persona: "Legacy Investor",
    icon: "invest",
  },
  {
    id: "commercial",
    label: "Commercial / Shop Plot",
    sub: "12 m to 30 m wide main road frontage",
    persona: "Commercial Buyer",
    icon: "commercial",
  },
  {
    id: "nri",
    label: "NRI / Future Asset",
    sub: "Clear 7/12 title with remote video & family tour",
    persona: "NRI Legacy Buyer",
    icon: "nri",
  },
];

const PLOT_SIZES = [
  "1,000 – 1,500 sq.ft",
  "1,500 – 2,200 sq.ft",
  "2,200+ sq.ft / Corner",
  "Commercial Frontage",
] as const;

const BUYING_TIMELINES = [
  "Within 30 Days",
  "1 – 3 Months",
  "3 – 6 Months",
  "Just Exploring Options",
] as const;

const FUNDING_PLANS = [
  { id: "self", label: "Self-Funded", loan: false },
  { id: "loan", label: "Bank Plot Loan", loan: true },
  { id: "both", label: "Part Self + Loan", loan: true },
] as const;

const TIME_SLOTS = [
  { time: "10:00 AM", tag: "Morning Light" },
  { time: "11:30 AM", tag: "Popular" },
  { time: "01:00 PM", tag: "Midday" },
  { time: "03:30 PM", tag: "Afternoon" },
  { time: "05:00 PM", tag: "Sunset View" },
] as const;

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function toIsoDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatReadableDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function getNextWeekendDate(from: Date, targetDay: 6 | 0): Date {
  const base = startOfDay(from);
  const diff = (targetDay - base.getDay() + 7) % 7 || 7;
  const result = new Date(base);
  result.setDate(base.getDate() + diff);
  return result;
}

export default function SiteVisitBookingModal({
  open,
  defaultProjectSlug,
  defaultPlotNumber,
  onClose,
  onSuccess,
}: Props) {
  const today = useMemo(() => startOfDay(new Date()), []);
  const tomorrow = useMemo(() => {
    const d = new Date(today);
    d.setDate(today.getDate() + 1);
    return d;
  }, [today]);

  const [step, setStep] = useState<1 | 2 | 3>(1);

  // Step 1 — Objective Qualifying Questionnaire
  const [projectSlug, setProjectSlug] = useState(defaultProjectSlug || PROJECTS[0].slug);
  const [goalId, setGoalId] = useState<string>(BUYING_GOALS[0].id);
  const [plotSize, setPlotSize] = useState<string>(PLOT_SIZES[0]);
  const [timeline, setTimeline] = useState<string>(BUYING_TIMELINES[0]);
  const [fundingId, setFundingId] = useState<(typeof FUNDING_PLANS)[number]["id"]>("loan");

  // Step 2 — Calendar & Time Slot
  const [calendarMonth, setCalendarMonth] = useState<Date>(
    () => new Date(tomorrow.getFullYear(), tomorrow.getMonth(), 1),
  );
  const [selectedDateIso, setSelectedDateIso] = useState<string>(() => toIsoDate(tomorrow));
  const [selectedSlot, setSelectedSlot] = useState<string>(TIME_SLOTS[1].time);
  const [visitMode, setVisitMode] = useState<"direct" | "pickup">("direct");

  // Step 3 — Visitor Contact Details
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [visitorCount, setVisitorCount] = useState("1 – 2 Visitors");
  const [notes, setNotes] = useState("");
  const [consent, setConsent] = useState(true);
  const [website, setWebsite] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep(1);
    setProjectSlug(defaultProjectSlug || PROJECTS[0].slug);
    setSelectedDateIso(toIsoDate(tomorrow));
    setCalendarMonth(new Date(tomorrow.getFullYear(), tomorrow.getMonth(), 1));
    setError(null);
  }, [open, defaultProjectSlug, tomorrow]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  const activeProject = PROJECTS.find((p) => p.slug === projectSlug) ?? PROJECTS[0];
  const activeGoal = BUYING_GOALS.find((g) => g.id === goalId) ?? BUYING_GOALS[0];
  const activeFunding = FUNDING_PLANS.find((f) => f.id === fundingId) ?? FUNDING_PLANS[1];

  // Build calendar cells for current displayed month
  const calendarDays = useMemo(() => {
    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();
    const firstWeekday = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells: Array<{ date: Date | null; iso: string; disabled: boolean; isWeekend: boolean }> = [];

    for (let i = 0; i < firstWeekday; i++) {
      cells.push({ date: null, iso: `pad-${i}`, disabled: true, isWeekend: false });
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const date = new Date(year, month, d);
      const dayOfWeek = date.getDay();
      cells.push({
        date,
        iso: toIsoDate(date),
        disabled: date.getTime() < today.getTime(),
        isWeekend: dayOfWeek === 0 || dayOfWeek === 6,
      });
    }
    return cells;
  }, [calendarMonth, today]);

  const canGoPrevMonth =
    calendarMonth.getFullYear() > today.getFullYear() ||
    (calendarMonth.getFullYear() === today.getFullYear() &&
      calendarMonth.getMonth() > today.getMonth());

  const maxDate = useMemo(() => new Date(today.getFullYear(), today.getMonth() + 3, 1), [today]);
  const canGoNextMonth =
    calendarMonth.getFullYear() < maxDate.getFullYear() ||
    (calendarMonth.getFullYear() === maxDate.getFullYear() &&
      calendarMonth.getMonth() < maxDate.getMonth());

  const quickDates = useMemo(() => {
    const sat = getNextWeekendDate(today, 6);
    const sun = getNextWeekendDate(today, 0);
    return [
      { label: "Tomorrow", date: tomorrow },
      { label: "This Saturday", date: sat },
      { label: "This Sunday", date: sun },
    ];
  }, [today, tomorrow]);

  if (!open) return null;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (fullName.trim().length < 2) {
      setError("Please enter your full name so our site advisor can greet you.");
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length < 10 || phoneDigits.length > 15) {
      setError("Please enter a valid 10-digit mobile or WhatsApp number.");
      return;
    }
    if (!consent) {
      setError("Please confirm communication consent to receive your site visit confirmation.");
      return;
    }

    setSubmitting(true);
    try {
      const structuredMessage = [
        `[GUIDED SITE VISIT SCHEDULED]`,
        `Date: ${formatReadableDate(selectedDateIso)} at ${selectedSlot}`,
        `Visit Mode: ${visitMode === "pickup" ? "Complimentary Nagpur Pick-up" : "Direct On-Site Guided Tour"}`,
        `Buying Objective: ${activeGoal.label}`,
        `Target Plot Size: ${plotSize}`,
        `Buying Timeline: ${timeline}`,
        `Funding Plan: ${activeFunding.label}`,
        `Group Size: ${visitorCount}`,
        notes.trim() ? `Visitor Note: ${notes.trim()}` : "",
      ]
        .filter(Boolean)
        .join(" | ");

      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fullName: fullName.trim(),
          phone: phone.trim(),
          email: email.trim(),
          persona: activeGoal.persona,
          interest: "site-visit",
          project: activeProject.slug,
          projectName: activeProject.name,
          plotNumber: defaultPlotNumber || "",
          estimatedBudgetInr: null,
          loanRequired: activeFunding.loan,
          message: structuredMessage,
          consent,
          website,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.error || "Could not schedule your site visit right now. Please try again.");
        return;
      }

      trackEvent({
        type: "cta_click",
        page: window.location.pathname,
        project: activeProject.slug,
        meta: `site_visit_booked_${selectedDateIso}`,
      });

      onSuccess({
        referenceCode: data.referenceCode || "RE-NGP-VISIT",
        fullName: fullName.trim(),
        projectSlug: activeProject.slug,
        projectName: activeProject.name,
        plotNumber: defaultPlotNumber || "",
        interest: "site-visit",
        followUpSequence: data.lead?.followUpSequence ?? [],
      });
    } catch {
      setError("Network error while booking your slot. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="site-visit-modal-title"
      data-lenis-prevent
      data-lenis-prevent-wheel
      data-lenis-prevent-touch
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-2 backdrop-blur-md sm:p-5"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <motion.div
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.34, ease: EASE_APPLE }}
        className="flex max-h-[92svh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-[#D4AF5F]/35 bg-[#F8F7F2] text-[#102018] shadow-2xl dark:bg-[#0B120F] dark:text-[#F4F1E7]"
      >
        {/* Top Apple-HIG Header & Step Progress */}
        <div className="shrink-0 border-b border-black/[0.08] bg-white/95 px-5 py-4 sm:px-7 dark:border-white/10 dark:bg-[#0F1713]/95">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-[#8C6D2E] dark:text-[#D4AF5F]">
                <CalendarCheck className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Guided On-Site Property Tour · Nagpur</span>
              </p>
              <h2
                id="site-visit-modal-title"
                className="mt-1 text-xl font-semibold tracking-tight text-[#102018] sm:text-2xl dark:text-white"
              >
                Book Your Free Site Visit
              </h2>
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close site visit scheduler"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-black/10 text-[#4F5D54] transition-colors hover:border-[#8C6D2E] hover:text-[#102018] dark:border-white/15 dark:text-[#B5C2B8] dark:hover:border-[#D4AF5F] dark:hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* 3-Step Apple Segmented Stepper */}
          <div className="mt-4 grid grid-cols-3 gap-2">
            {[
              { num: 1 as const, label: "1. Requirements" },
              { num: 2 as const, label: "2. Date & Slot" },
              { num: 3 as const, label: "3. Confirm Tour" },
            ].map((item) => {
              const isCurrent = step === item.num;
              const isDone = step > item.num;
              return (
                <button
                  key={item.num}
                  type="button"
                  onClick={() => {
                    if (item.num < step) setStep(item.num);
                  }}
                  disabled={item.num > step}
                  className={`flex items-center justify-center gap-1.5 rounded-xl border px-2.5 py-2 text-[11px] font-semibold transition-all ${
                    isCurrent
                      ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                      : isDone
                        ? "border-[#2E6B42]/40 bg-[#E8F5ED] text-[#1E5C35] dark:border-[#3DA866]/35 dark:bg-[#13261B] dark:text-[#7CF0A6]"
                        : "border-black/[0.08] bg-[#F3F1E9] text-[#6B7970] dark:border-white/10 dark:bg-[#121C17] dark:text-[#7C8C82]"
                  }`}
                >
                  {isDone ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> : null}
                  <span className="truncate">{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Scrollable Body — Trackpad & Touch Safe */}
        <div
          data-lenis-prevent
          data-lenis-prevent-wheel
          data-lenis-prevent-touch
          className="modal-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-7 sm:py-6"
        >
          <AnimatePresence mode="wait">
            {/* STEP 1: QUALIFYING OBJECTIVE QUESTIONNAIRE */}
            {step === 1 ? (
              <motion.div
                key="visit-step-1"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.24, ease: EASE_APPLE }}
                className="space-y-6"
              >
                {/* Q1: Choose Nagpur Estate */}
                <div>
                  <p className="text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                    1. Which Nagpur layout would you like to visit?
                  </p>
                  <div className="mt-2.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    {PROJECTS.map((proj) => {
                      const active = projectSlug === proj.slug;
                      const stats = summarizeLayout(proj.layout);
                      return (
                        <button
                          key={proj.slug}
                          type="button"
                          onClick={() => setProjectSlug(proj.slug)}
                          className={`flex flex-col rounded-2xl border p-3.5 text-left transition-all ${
                            active
                              ? "border-[#10281E] bg-[#10281E] text-white shadow-sm dark:border-[#D4AF5F] dark:bg-[#15241D]"
                              : "border-black/10 bg-white hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:hover:border-[#D4AF5F]/60"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span
                              className={`text-sm font-semibold ${
                                active ? "text-white" : "text-[#102018] dark:text-[#F4F1E7]"
                              }`}
                            >
                              {proj.name}
                            </span>
                            {active ? (
                              <CheckCircle2
                                className="h-4 w-4 shrink-0 text-[#E5C77B]"
                                aria-hidden="true"
                              />
                            ) : null}
                          </div>
                          <span
                            className={`mt-1 inline-flex items-center gap-1 text-[11px] ${
                              active ? "text-[#D4AF5F]" : "text-[#637269] dark:text-[#96A69B]"
                            }`}
                          >
                            <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                            <span className="truncate">{proj.corridor}</span>
                          </span>
                          <span
                            className={`font-mono-tabular mt-2 text-[11px] font-medium ${
                              active ? "text-[#DCE5DF]" : "text-[#3D4D43] dark:text-[#B4C2B8]"
                            }`}
                          >
                            From {formatINR(proj.baseRatePerSqFt)}*/sq.ft · {stats.available} open
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Q2: Primary Buying Purpose */}
                <div>
                  <p className="text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                    2. What is your primary objective for this plot?
                  </p>
                  <div className="mt-2.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    {BUYING_GOALS.map((goal) => {
                      const active = goalId === goal.id;
                      const Icon =
                        goal.icon === "home"
                          ? Home
                          : goal.icon === "invest"
                            ? TrendingUp
                            : goal.icon === "commercial"
                              ? Building2
                              : Compass;
                      return (
                        <button
                          key={goal.id}
                          type="button"
                          onClick={() => setGoalId(goal.id)}
                          className={`flex items-start gap-3 rounded-2xl border p-3.5 text-left transition-all ${
                            active
                              ? "border-[#8C6D2E] bg-[#FDF9EE] ring-1 ring-[#8C6D2E]/30 dark:border-[#D4AF5F] dark:bg-[#17231C] dark:ring-[#D4AF5F]/30"
                              : "border-black/10 bg-white hover:border-black/25 dark:border-white/10 dark:bg-[#101814] dark:hover:border-white/25"
                          }`}
                        >
                          <span
                            className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
                              active
                                ? "bg-[#10281E] text-[#E5C77B] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                                : "bg-[#EFECE3] text-[#6E5623] dark:bg-[#16241D] dark:text-[#D4AF5F]"
                            }`}
                          >
                            <Icon className="h-4 w-4" aria-hidden="true" />
                          </span>
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-[#102018] dark:text-white">
                              {goal.label}
                            </p>
                            <p className="mt-0.5 text-[11px] leading-snug text-[#58675E] dark:text-[#98A89D]">
                              {goal.sub}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Q3: Target Plot Size & Buying Timeline */}
                <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                      3. Preferred Plot Size
                    </p>
                    <div className="mt-2.5 grid grid-cols-2 gap-2">
                      {PLOT_SIZES.map((sz) => {
                        const active = plotSize === sz;
                        return (
                          <button
                            key={sz}
                            type="button"
                            onClick={() => setPlotSize(sz)}
                            className={`min-h-[42px] rounded-xl border px-2.5 py-2 text-[11px] font-semibold transition-all ${
                              active
                                ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                                : "border-black/10 bg-white text-[#3F4E45] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#B5C2B8]"
                            }`}
                          >
                            {sz}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                      4. Expected Buying Timeline
                    </p>
                    <div className="mt-2.5 grid grid-cols-2 gap-2">
                      {BUYING_TIMELINES.map((tl) => {
                        const active = timeline === tl;
                        return (
                          <button
                            key={tl}
                            type="button"
                            onClick={() => setTimeline(tl)}
                            className={`min-h-[42px] rounded-xl border px-2.5 py-2 text-[11px] font-semibold transition-all ${
                              active
                                ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                                : "border-black/10 bg-white text-[#3F4E45] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#B5C2B8]"
                            }`}
                          >
                            {tl}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Q5: Funding Plan */}
                <div>
                  <p className="text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                    5. How do you plan to fund the plot?
                  </p>
                  <div className="mt-2.5 grid grid-cols-3 gap-2">
                    {FUNDING_PLANS.map((fp) => {
                      const active = fundingId === fp.id;
                      return (
                        <button
                          key={fp.id}
                          type="button"
                          onClick={() => setFundingId(fp.id)}
                          className={`inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold transition-all ${
                            active
                              ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                              : "border-black/10 bg-white text-[#3F4E45] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#B5C2B8]"
                          }`}
                        >
                          {fp.loan ? <Landmark className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> : null}
                          <span className="truncate">{fp.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </motion.div>
            ) : null}

            {/* STEP 2: INTERACTIVE CALENDAR & TIME SLOT PICKER */}
            {step === 2 ? (
              <motion.div
                key="visit-step-2"
                initial={{ opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -14 }}
                transition={{ duration: 0.24, ease: EASE_APPLE }}
                className="space-y-6"
              >
                {/* Quick Date Shortcuts */}
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#102018] dark:text-white">
                    <Calendar className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                    <span>Choose Your Preferred Visit Date</span>
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {quickDates.map((qd) => {
                      const iso = toIsoDate(qd.date);
                      const active = selectedDateIso === iso;
                      return (
                        <button
                          key={qd.label}
                          type="button"
                          onClick={() => {
                            setSelectedDateIso(iso);
                            setCalendarMonth(new Date(qd.date.getFullYear(), qd.date.getMonth(), 1));
                          }}
                          className={`rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                            active
                              ? "border-[#10281E] bg-[#10281E] text-white dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                              : "border-black/10 bg-white text-[#46564C] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#B4C2B8]"
                          }`}
                        >
                          {qd.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Apple-HIG Interactive Monthly Calendar */}
                <div className="rounded-2xl border border-black/[0.08] bg-white p-4 sm:p-5 dark:border-white/10 dark:bg-[#101814]">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-[#102018] dark:text-white">
                      {calendarMonth.toLocaleDateString("en-IN", {
                        month: "long",
                        year: "numeric",
                      })}
                    </h3>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={!canGoPrevMonth}
                        onClick={() =>
                          setCalendarMonth(
                            new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() - 1, 1),
                          )
                        }
                        aria-label="Previous month"
                        className="flex h-8 w-8 items-center justify-center rounded-lg border border-black/10 text-[#102018] transition-colors hover:border-[#8C6D2E] disabled:opacity-35 dark:border-white/15 dark:text-white"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        disabled={!canGoNextMonth}
                        onClick={() =>
                          setCalendarMonth(
                            new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 1),
                          )
                        }
                        aria-label="Next month"
                        className="flex h-8 w-8 items-center justify-center rounded-lg border border-black/10 text-[#102018] transition-colors hover:border-[#8C6D2E] disabled:opacity-35 dark:border-white/15 dark:text-white"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>

                  {/* Weekday Header */}
                  <div className="mt-4 grid grid-cols-7 gap-1 text-center text-[11px] font-semibold text-[#6F7E75] dark:text-[#84958A]">
                    {WEEKDAYS.map((wd) => (
                      <span key={wd} className="py-1">
                        {wd}
                      </span>
                    ))}
                  </div>

                  {/* Day Grid */}
                  <div className="mt-1 grid grid-cols-7 gap-1.5">
                    {calendarDays.map((cell) => {
                      if (!cell.date) {
                        return <span key={cell.iso} className="h-10" aria-hidden="true" />;
                      }
                      const isSelected = selectedDateIso === cell.iso;
                      const isToday = cell.iso === toIsoDate(today);
                      return (
                        <button
                          key={cell.iso}
                          type="button"
                          disabled={cell.disabled}
                          onClick={() => setSelectedDateIso(cell.iso)}
                          className={`font-mono-tabular relative flex h-10 w-full items-center justify-center rounded-xl text-xs font-semibold transition-all ${
                            cell.disabled
                              ? "cursor-not-allowed text-[#B5BEB8] dark:text-[#3A4840]"
                              : isSelected
                                ? "bg-[#10281E] text-[#F6F5F0] shadow-sm dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                                : isToday
                                  ? "border border-[#8C6D2E]/50 bg-[#FDF8EA] text-[#102018] hover:border-[#8C6D2E] dark:border-[#D4AF5F]/50 dark:bg-[#19261E] dark:text-[#E5C77B]"
                                  : cell.isWeekend
                                    ? "bg-[#F4F2EA] text-[#102018] hover:bg-[#E7E3D5] dark:bg-[#15211B] dark:text-[#E5C77B] dark:hover:bg-[#1C2C24]"
                                    : "text-[#102018] hover:bg-[#F1EFE7] dark:text-[#DCE5DF] dark:hover:bg-[#16231C]"
                          }`}
                        >
                          {cell.date.getDate()}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Time Slot Selector */}
                <div>
                  <p className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#102018] dark:text-white">
                    <Clock className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                    <span>Select Time Slot ({formatReadableDate(selectedDateIso)})</span>
                  </p>
                  <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-5">
                    {TIME_SLOTS.map((slot) => {
                      const active = selectedSlot === slot.time;
                      return (
                        <button
                          key={slot.time}
                          type="button"
                          onClick={() => setSelectedSlot(slot.time)}
                          className={`flex flex-col items-center justify-center rounded-xl border px-2.5 py-2.5 text-center transition-all ${
                            active
                              ? "border-[#10281E] bg-[#10281E] text-white dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                              : "border-black/10 bg-white text-[#102018] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#EFECE2]"
                          }`}
                        >
                          <span className="font-mono-tabular text-xs font-semibold">
                            {slot.time}
                          </span>
                          <span
                            className={`mt-0.5 text-[10px] ${
                              active
                                ? "text-[#E5C77B] dark:text-[#1A261E]"
                                : "text-[#6A7970] dark:text-[#8E9F93]"
                            }`}
                          >
                            {slot.tag}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Visit Mode */}
                <div>
                  <p className="text-xs font-semibold text-[#102018] dark:text-white">
                    Preferred Tour Arrangement
                  </p>
                  <div className="mt-2.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => setVisitMode("direct")}
                      className={`flex items-start gap-3 rounded-2xl border p-3.5 text-left transition-all ${
                        visitMode === "direct"
                          ? "border-[#8C6D2E] bg-[#FDF9EE] dark:border-[#D4AF5F] dark:bg-[#17231C]"
                          : "border-black/10 bg-white dark:border-white/10 dark:bg-[#101814]"
                      }`}
                    >
                      <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                      <div>
                        <p className="text-xs font-semibold text-[#102018] dark:text-white">
                          Direct On-Site Arrival
                        </p>
                        <p className="mt-0.5 text-[11px] text-[#5B6A61] dark:text-[#98A89D]">
                          Receive exact Google Maps pin &amp; meet our layout engineer on-site.
                        </p>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => setVisitMode("pickup")}
                      className={`flex items-start gap-3 rounded-2xl border p-3.5 text-left transition-all ${
                        visitMode === "pickup"
                          ? "border-[#8C6D2E] bg-[#FDF9EE] dark:border-[#D4AF5F] dark:bg-[#17231C]"
                          : "border-black/10 bg-white dark:border-white/10 dark:bg-[#101814]"
                      }`}
                    >
                      <Car className="mt-0.5 h-4 w-4 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                      <div>
                        <p className="text-xs font-semibold text-[#102018] dark:text-white">
                          Complimentary Nagpur Pick-up
                        </p>
                        <p className="mt-0.5 text-[11px] text-[#5B6A61] dark:text-[#98A89D]">
                          AC cab pick-up &amp; drop from Nagpur city for your family visit.
                        </p>
                      </div>
                    </button>
                  </div>
                </div>
              </motion.div>
            ) : null}

            {/* STEP 3: VISITOR DETAILS & CONFIRMATION */}
            {step === 3 ? (
              <motion.form
                id="site-visit-confirm-form"
                key="visit-step-3"
                initial={{ opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -14 }}
                transition={{ duration: 0.24, ease: EASE_APPLE }}
                onSubmit={handleSubmit}
                className="space-y-5"
              >
                {/* Live Tour Summary Card */}
                <div className="rounded-2xl border border-[#D4AF5F]/35 bg-[#10241B] p-4 text-[#F5F2E9] dark:bg-[#121F19]">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[11px] font-semibold text-[#E5C77B]">
                      Your Scheduled Tour Summary
                    </span>
                    <button
                      type="button"
                      onClick={() => setStep(2)}
                      className="text-[11px] font-medium text-[#B8C7BD] underline underline-offset-2 hover:text-white"
                    >
                      Change Date / Slot
                    </button>
                  </div>
                  <p className="mt-1.5 text-base font-semibold text-white">
                    {activeProject.name}
                    {defaultPlotNumber ? ` · Plot ${defaultPlotNumber}` : ""}
                  </p>
                  <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-[#D4DFD7]">
                    <span className="inline-flex items-center gap-1.5">
                      <Calendar className="h-3.5 w-3.5 text-[#D4AF5F]" />
                      {formatReadableDate(selectedDateIso)}
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <Clock className="h-3.5 w-3.5 text-[#D4AF5F]" />
                      {selectedSlot}
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <ShieldCheck className="h-3.5 w-3.5 text-[#D4AF5F]" />
                      {activeGoal.label} · {plotSize}
                    </span>
                  </div>
                </div>

                {/* Honeypot */}
                <div className="hidden" aria-hidden="true">
                  <label htmlFor="sv-website">Website</label>
                  <input
                    id="sv-website"
                    type="text"
                    tabIndex={-1}
                    autoComplete="off"
                    value={website}
                    onChange={(e) => setWebsite(e.target.value)}
                  />
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="sv-name"
                      className="block text-xs font-semibold text-[#18261F] dark:text-[#E8E5DA]"
                    >
                      Full Name *
                    </label>
                    <input
                      id="sv-name"
                      type="text"
                      required
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="e.g. Rajesh Deshmukh"
                      className="mt-1.5 min-h-[46px] w-full rounded-xl border border-black/15 bg-white px-3.5 text-sm text-[#102018] placeholder:text-[#7E8C84] focus:border-[#8C6D2E] focus:outline-none dark:border-white/15 dark:bg-[#101814] dark:text-white dark:focus:border-[#D4AF5F]"
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="sv-phone"
                      className="block text-xs font-semibold text-[#18261F] dark:text-[#E8E5DA]"
                    >
                      Mobile / WhatsApp Number *
                    </label>
                    <input
                      id="sv-phone"
                      type="tel"
                      required
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      placeholder="+91 98XXX XXXXX"
                      className="mt-1.5 min-h-[46px] w-full rounded-xl border border-black/15 bg-white px-3.5 text-sm text-[#102018] placeholder:text-[#7E8C84] focus:border-[#8C6D2E] focus:outline-none dark:border-white/15 dark:bg-[#101814] dark:text-white dark:focus:border-[#D4AF5F]"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="sv-email"
                      className="block text-xs font-semibold text-[#18261F] dark:text-[#E8E5DA]"
                    >
                      Email (Optional — for Calendar Invite)
                    </label>
                    <input
                      id="sv-email"
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="name@example.com"
                      className="mt-1.5 min-h-[46px] w-full rounded-xl border border-black/15 bg-white px-3.5 text-sm text-[#102018] placeholder:text-[#7E8C84] focus:border-[#8C6D2E] focus:outline-none dark:border-white/15 dark:bg-[#101814] dark:text-white dark:focus:border-[#D4AF5F]"
                    />
                  </div>

                  <div>
                    <p className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#18261F] dark:text-[#E8E5DA]">
                      <Users className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
                      <span>Visitors Joining</span>
                    </p>
                    <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                      {["1 – 2 Visitors", "3 – 4 Family", "5+ Group"].map((grp) => (
                        <button
                          key={grp}
                          type="button"
                          onClick={() => setVisitorCount(grp)}
                          className={`min-h-[46px] rounded-xl border px-2 text-[11px] font-semibold transition-colors ${
                            visitorCount === grp
                              ? "border-[#10281E] bg-[#10281E] text-white dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                              : "border-black/15 bg-white text-[#46554C] dark:border-white/15 dark:bg-[#101814] dark:text-[#B4C2B8]"
                          }`}
                        >
                          {grp.split(" ")[0]} {grp.split(" ")[1]}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="sv-notes"
                    className="block text-xs font-semibold text-[#18261F] dark:text-[#E8E5DA]"
                  >
                    Pick-up Area or Specific Plot Requests (Optional)
                  </label>
                  <input
                    id="sv-notes"
                    type="text"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder={
                      visitMode === "pickup"
                        ? "e.g. Pick-up from Dharampeth / Wardha Road / MIHAN"
                        : "e.g. East-facing corner plot or near park"
                    }
                    className="mt-1.5 min-h-[44px] w-full rounded-xl border border-black/15 bg-white px-3.5 text-sm text-[#102018] placeholder:text-[#7E8C84] focus:border-[#8C6D2E] focus:outline-none dark:border-white/15 dark:bg-[#101814] dark:text-white dark:focus:border-[#D4AF5F]"
                  />
                </div>

                <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-black/[0.08] bg-white p-3 text-xs leading-relaxed text-[#4F5D54] dark:border-white/10 dark:bg-[#101814] dark:text-[#A4B3A8]">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-[#D4AF5F]"
                  />
                  <span>
                    I authorize Realtor Estates to confirm my scheduled Nagpur site visit and send
                    the live site location pin &amp; NMRDA sanction checklist via WhatsApp / Call.
                  </span>
                </label>

                {error ? (
                  <p
                    role="alert"
                    className="rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-2.5 text-xs font-medium text-red-700 dark:text-red-300"
                  >
                    {error}
                  </p>
                ) : null}
              </motion.form>
            ) : null}
          </AnimatePresence>
        </div>

        {/* Sticky Bottom Navigation Bar */}
        <div className="shrink-0 border-t border-black/[0.08] bg-white/95 px-5 py-4 sm:px-7 dark:border-white/10 dark:bg-[#0F1713]/95">
          <div className="flex items-center justify-between gap-3">
            {step > 1 ? (
              <button
                type="button"
                onClick={() => setStep((s) => (s === 3 ? 2 : 1))}
                className="inline-flex min-h-[46px] items-center gap-1.5 rounded-xl border border-black/15 px-4 py-2.5 text-xs font-semibold text-[#102018] transition-colors hover:border-[#8C6D2E] dark:border-white/15 dark:text-[#EFECE2] dark:hover:border-[#D4AF5F]"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Back</span>
              </button>
            ) : (
              <p className="hidden text-[11px] text-[#5F6E65] sm:block dark:text-[#8E9F93]">
                100% Free · Zero Obligation · Original 7/12 Shown On-Site
              </p>
            )}

            {step === 1 ? (
              <button
                type="button"
                onClick={() => setStep(2)}
                className="ml-auto inline-flex min-h-[46px] items-center gap-2 rounded-xl bg-[#10281E] px-5 py-2.5 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
              >
                <span>Choose Date &amp; Time Slot</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : step === 2 ? (
              <button
                type="button"
                onClick={() => setStep(3)}
                className="ml-auto inline-flex min-h-[46px] items-center gap-2 rounded-xl bg-[#10281E] px-5 py-2.5 text-xs font-semibold text-[#F6F5F0] transition-colors hover:bg-[#193b2d] dark:bg-[#D4AF5F] dark:text-[#080C0A] dark:hover:bg-[#e5c77b]"
              >
                <span>Continue to Confirm</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : (
              <button
                type="submit"
                form="site-visit-confirm-form"
                disabled={submitting}
                className="ml-auto inline-flex min-h-[46px] items-center gap-2 rounded-xl bg-[#D4AF5F] px-6 py-2.5 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b] disabled:opacity-60"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    <span>Booking Your Slot…</span>
                  </>
                ) : (
                  <>
                    <CalendarCheck className="h-4 w-4" aria-hidden="true" />
                    <span>Confirm Free Site Visit</span>
                  </>
                )}
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
}
