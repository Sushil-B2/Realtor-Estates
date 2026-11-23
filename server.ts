import "dotenv/config";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import express from "express";
import { createServer as createViteServer } from "vite";

type FollowUpStep = {
  step: number;
  dayOffset: number;
  subject: string;
  channel: "Email" | "Concierge Call";
  status: "Sent" | "Scheduled";
  scheduledFor: string;
};

type LeadRecord = {
  id: string;
  referenceCode: string;
  fullName: string;
  phoneMasked: string;
  emailMasked: string;
  persona: string;
  interest: string;
  project: string;
  plotNumber: string;
  estimatedBudgetInr: number | null;
  loanRequired: boolean;
  message: string;
  crmSyncStatus: "Synced to Webhook" | "Queued in Local CRM";
  followUpSequence: FollowUpStep[];
  createdAt: string;
};

type HeatmapPoint = {
  page: string;
  xPct: number;
  yPct: number;
  kind: "click" | "plot-inspect" | "cta";
  ts: string;
};

type AnalyticsEvent = {
  id: string;
  type: "pageview" | "plot_360_open" | "calculator_update" | "lead_magnet" | "cta_click" | "scroll_depth";
  page: string;
  project?: string;
  plotNumber?: string;
  meta?: string;
  ts: string;
};

type StoreData = {
  leads: LeadRecord[];
  events: AnalyticsEvent[];
  heatmap: HeatmapPoint[];
  leadMagnetDownloads: number;
};

const PORT = Number(process.env.PORT || 3000);
const DATA_DIR = path.resolve(process.cwd(), ".data");
const STORE_PATH = path.resolve(DATA_DIR, "realtor-estates-store.json");

function ensureStoreDir() {
  if (!STORE_PATH.startsWith(DATA_DIR + path.sep)) {
    throw new Error("Invalid store path configuration");
  }
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
}

function defaultStore(): StoreData {
  const now = new Date().toISOString();
  return {
    leads: [],
    events: [
      { id: "seed-1", type: "pageview", page: "/", ts: now },
    ],
    heatmap: [],
    leadMagnetDownloads: 0,
  };
}

function loadStore(): StoreData {
  try {
    ensureStoreDir();
    if (!fs.existsSync(STORE_PATH)) {
      const initial = defaultStore();
      fs.writeFileSync(STORE_PATH, JSON.stringify(initial, null, 2), "utf8");
      return initial;
    }
    const raw = fs.readFileSync(STORE_PATH, "utf8");
    const parsed = JSON.parse(raw) as Partial<StoreData>;
    return {
      leads: Array.isArray(parsed.leads) ? parsed.leads : [],
      events: Array.isArray(parsed.events) ? parsed.events : [],
      heatmap: Array.isArray(parsed.heatmap) ? parsed.heatmap : [],
      leadMagnetDownloads: typeof parsed.leadMagnetDownloads === "number" ? parsed.leadMagnetDownloads : 0,
    };
  } catch {
    return defaultStore();
  }
}

function saveStore(store: StoreData) {
  try {
    ensureStoreDir();
    const tmpPath = path.resolve(DATA_DIR, `store.${crypto.randomBytes(4).toString("hex")}.tmp`);
    fs.writeFileSync(tmpPath, JSON.stringify(store, null, 2), "utf8");
    fs.renameSync(tmpPath, STORE_PATH);
  } catch (err) {
    console.error("Failed to persist store:", err instanceof Error ? err.message : "unknown error");
  }
}

const store: StoreData = loadStore();

function cleanString(input: unknown, maxLen: number): string {
  if (typeof input !== "string") return "";
  return input.trim().replace(/[\u0000-\u001F\u007F]/g, "").slice(0, maxLen);
}

function maskPhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 4) return "****";
  return `${"*".repeat(Math.max(0, digits.length - 4))}${digits.slice(-4)}`;
}

function maskEmail(email: string): string {
  const idx = email.indexOf("@");
  if (idx <= 1) return email ? "***" : "Not provided";
  return `${email.slice(0, 2)}***${email.slice(idx)}`;
}

function isSafeWebhookUrl(rawUrl: string): boolean {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "https:") return false;
    const host = parsed.hostname.toLowerCase();
    if (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "::1" ||
      host === "169.254.169.254" ||
      host.startsWith("10.") ||
      host.startsWith("192.168.") ||
      /^172\.(1[6-9]|2\d|3[0-1])\./.test(host) ||
      host.endsWith(".local") ||
      host.endsWith(".internal")
    ) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function buildFollowUpSequence(projectName: string, plotNumber: string): FollowUpStep[] {
  const now = Date.now();
  const plotLabel = plotNumber ? ` — Plot ${plotNumber}` : "";
  return [
    {
      step: 1,
      dayOffset: 0,
      subject: `Your ${projectName}${plotLabel} Dossier & Written Availability Sheet`,
      channel: "Email",
      status: "Sent",
      scheduledFor: new Date(now).toISOString(),
    },
    {
      step: 2,
      dayOffset: 2,
      subject: `Nagpur Corridor Title & 7/12 Verification Checklist (${projectName})`,
      channel: "Email",
      status: "Scheduled",
      scheduledFor: new Date(now + 2 * 86400_000).toISOString(),
    },
    {
      step: 3,
      dayOffset: 5,
      subject: `Private On-Site Boundary Walkthrough & Bank Loan Pre-Qualification`,
      channel: "Concierge Call",
      status: "Scheduled",
      scheduledFor: new Date(now + 5 * 86400_000).toISOString(),
    },
  ];
}

async function dispatchToCrmWebhook(payload: Record<string, unknown>): Promise<boolean> {
  const webhookUrl = process.env.CRM_WEBHOOK_URL?.trim();
  if (!webhookUrl || !isSafeWebhookUrl(webhookUrl)) {
    return false;
  }
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const apiKey = process.env.CRM_API_KEY?.trim();
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3500);
    const response = await fetch(webhookUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timer);
    return response.ok;
  } catch {
    return false;
  }
}

async function startServer() {
  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "64kb" }));

  // Security, technical SEO & crawl hygiene headers compatible with iframe previews
  app.use((req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
    res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    if (
      req.path.startsWith("/api/") ||
      req.path === "/plot-buyer-checklist.html" ||
      /^\/projects\/[^/]+\/thank-you\/?$/.test(req.path)
    ) {
      res.setHeader("X-Robots-Tag", "noindex, nofollow");
    }
    next();
  });

  // White-hat HTTP 301 permanent redirects from legacy slugs to canonical project URLs
  const legacyRedirects: Record<string, string> = {
    "/projects/sadaj-110": "/projects/samruddhi-estate-phase-3",
    "/projects/sajal-111": "/projects/samruddhi-estate-phase-4",
    "/projects/samruddhi-estate": "/projects/bhagirath-nagari",
    "/projects/bhagirathi-nagar-4": "/projects/bhagirath-nagari",
  };
  app.use((req, res, next) => {
    const target = legacyRedirects[req.path.replace(/\/$/, "")];
    if (target) {
      const qs = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
      res.redirect(301, `${target}${qs}`);
      return;
    }
    next();
  });

  // Dynamic canonical XML sitemap for Googlebot & Answer Engines
  app.get("/sitemap.xml", (req, res) => {
    const baseUrl =
      process.env.APP_URL?.trim().replace(/\/$/, "") ||
      `${req.protocol}://${req.get("host")}`;
    const today = new Date().toISOString().slice(0, 10);
    const urls = [
      { loc: `${baseUrl}/`, priority: "1.0", freq: "weekly" },
      { loc: `${baseUrl}/projects/samruddhi-estate-phase-3`, priority: "0.9", freq: "weekly" },
      { loc: `${baseUrl}/projects/samruddhi-estate-phase-4`, priority: "0.9", freq: "weekly" },
      { loc: `${baseUrl}/projects/bhagirath-nagari`, priority: "0.9", freq: "weekly" },
      { loc: `${baseUrl}/projects/ananta-akasha`, priority: "0.9", freq: "weekly" },
      { loc: `${baseUrl}/privacy-policy`, priority: "0.4", freq: "monthly" },
      { loc: `${baseUrl}/terms-and-conditions`, priority: "0.4", freq: "monthly" },
    ];
    const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
      .map(
        (u) =>
          `  <url>\n    <loc>${u.loc}</loc>\n    <lastmod>${today}</lastmod>\n    <changefreq>${u.freq}</changefreq>\n    <priority>${u.priority}</priority>\n  </url>`,
      )
      .join("\n")}\n</urlset>\n`;
    res.setHeader("Content-Type", "application/xml; charset=utf-8");
    res.setHeader("Cache-Control", "public, max-age=3600");
    res.send(xml);
  });

  app.get("/api/health", (_req, res) => {
    res.json({ status: "ok", brand: "Realtor Estates" });
  });

  app.post("/api/leads", async (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    // Silent spam honeypot
    if (cleanString(body.website, 100).length > 0) {
      res.status(200).json({
        ok: true,
        referenceCode: "RE-NGP-SPAM",
        message: "Thank you. Your request has been received.",
      });
      return;
    }

    const fullName = cleanString(body.fullName, 120);
    const phone = cleanString(body.phone, 32);
    const email = cleanString(body.email, 160);
    const persona = cleanString(body.persona, 80) || "Legacy Investor";
    const interest = cleanString(body.interest, 40) || "site-visit";
    const project = cleanString(body.project, 80) || "samruddhi-estate";
    const projectName = cleanString(body.projectName, 120) || "Realtor Estates Nagpur";
    const plotNumber = cleanString(body.plotNumber, 24);
    const message = cleanString(body.message, 1200);
    const consent = body.consent === true;
    const loanRequired = body.loanRequired === true;
    const rawBudget = Number(body.estimatedBudgetInr);
    const estimatedBudgetInr = Number.isFinite(rawBudget) && rawBudget > 0 && rawBudget < 5_000_000_000 ? Math.round(rawBudget) : null;

    if (fullName.length < 2) {
      res.status(400).json({ error: "Please enter your full name (at least 2 characters)." });
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length < 10 || phoneDigits.length > 15) {
      res.status(400).json({ error: "Please provide a valid 10-digit phone number (with optional country code)." });
      return;
    }
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      res.status(400).json({ error: "Please enter a valid email address or leave it blank." });
      return;
    }
    if (!consent) {
      res.status(400).json({ error: "Please confirm communication consent so our advisor may reply." });
      return;
    }

    const referenceCode = `RE-${project.slice(0, 3).toUpperCase()}-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
    const followUpSequence = buildFollowUpSequence(projectName, plotNumber);

    const webhookSynced = await dispatchToCrmWebhook({
      referenceCode,
      fullName,
      phone,
      email,
      persona,
      interest,
      project,
      plotNumber,
      estimatedBudgetInr,
      loanRequired,
      message,
      createdAt: new Date().toISOString(),
    });

    const record: LeadRecord = {
      id: crypto.randomUUID(),
      referenceCode,
      fullName,
      phoneMasked: maskPhone(phone),
      emailMasked: maskEmail(email),
      persona,
      interest,
      project,
      plotNumber,
      estimatedBudgetInr,
      loanRequired,
      message,
      crmSyncStatus: webhookSynced ? "Synced to Webhook" : "Queued in Local CRM",
      followUpSequence,
      createdAt: new Date().toISOString(),
    };

    store.leads.unshift(record);
    if (store.leads.length > 250) store.leads.length = 250;
    saveStore(store);

    res.status(201).json({
      ok: true,
      referenceCode,
      lead: record,
      message: "Your enquiry is confirmed. A senior Nagpur estate advisor will send written plot verification shortly.",
    });
  });

  app.get("/api/leads", (_req, res) => {
    res.json({
      total: store.leads.length,
      leadMagnetDownloads: store.leadMagnetDownloads,
      leads: store.leads.slice(0, 40),
    });
  });

  app.post("/api/lead-magnet", (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const project = cleanString(body.project, 80) || "portfolio";
    store.leadMagnetDownloads += 1;
    store.events.unshift({
      id: crypto.randomUUID(),
      type: "lead_magnet",
      page: cleanString(body.page, 120) || "/",
      project,
      meta: "Maharashtra Plot Buyer Checklist",
      ts: new Date().toISOString(),
    });
    if (store.events.length > 600) store.events.length = 600;
    saveStore(store);
    res.json({ ok: true, downloads: store.leadMagnetDownloads });
  });

  app.post("/api/analytics/event", (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const allowedTypes = new Set([
      "pageview",
      "plot_360_open",
      "calculator_update",
      "lead_magnet",
      "cta_click",
      "scroll_depth",
    ]);
    const type = cleanString(body.type, 32) as AnalyticsEvent["type"];
    if (!allowedTypes.has(type)) {
      res.status(400).json({ error: "Unsupported analytics event type." });
      return;
    }
    const page = cleanString(body.page, 140) || "/";
    const project = cleanString(body.project, 80) || undefined;
    const plotNumber = cleanString(body.plotNumber, 24) || undefined;
    const meta = cleanString(body.meta, 120) || undefined;

    store.events.unshift({
      id: crypto.randomUUID(),
      type,
      page,
      project,
      plotNumber,
      meta,
      ts: new Date().toISOString(),
    });
    if (store.events.length > 600) store.events.length = 600;

    const xPct = Number(body.xPct);
    const yPct = Number(body.yPct);
    if (Number.isFinite(xPct) && Number.isFinite(yPct) && xPct >= 0 && xPct <= 100 && yPct >= 0 && yPct <= 100) {
      store.heatmap.unshift({
        page,
        xPct: Math.round(xPct * 10) / 10,
        yPct: Math.round(yPct * 10) / 10,
        kind: type === "plot_360_open" ? "plot-inspect" : type === "cta_click" ? "cta" : "click",
        ts: new Date().toISOString(),
      });
      if (store.heatmap.length > 400) store.heatmap.length = 400;
    }

    saveStore(store);
    res.json({ ok: true });
  });

  app.get("/api/analytics/summary", (_req, res) => {
    const pageViews = store.events.filter((e) => e.type === "pageview").length;
    const plotInspections = store.events.filter((e) => e.type === "plot_360_open").length;
    const calculatorRuns = store.events.filter((e) => e.type === "calculator_update").length;
    const ctaClicks = store.events.filter((e) => e.type === "cta_click").length;

    const plotInterestMap = new Map<string, number>();
    for (const ev of store.events) {
      if (ev.type === "plot_360_open" && ev.plotNumber) {
        const key = `${ev.project || "estate"} · Plot ${ev.plotNumber}`;
        plotInterestMap.set(key, (plotInterestMap.get(key) || 0) + 1);
      }
    }
    const topPlots = Array.from(plotInterestMap.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label, views]) => ({ label, views }));

    res.json({
      pageViews,
      plotInspections,
      calculatorRuns,
      ctaClicks,
      leadCount: store.leads.length,
      leadMagnetDownloads: store.leadMagnetDownloads,
      conversionRatePct: pageViews > 0 ? Number(((store.leads.length / pageViews) * 100).toFixed(1)) : 0,
      topPlots,
      heatmapPoints: store.heatmap.slice(0, 150),
      recentEvents: store.events.slice(0, 20),
    });
  });

  type RouteSeoMeta = {
    title: string;
    description: string;
    robots: string;
    canonicalPath: string;
    ogImagePath: string;
    jsonLd?: Record<string, unknown>;
  };

  const PROJECT_SEO_MAP: Record<
    string,
    {
      name: string;
      khNo: string;
      corridor: string;
      location: string;
      resRate: number;
      comRate?: number;
      heroImage: string;
      sanctionRef: string;
      frontage: string;
      faqQ: string;
      faqA: string;
    }
  > = {
    "samruddhi-estate-phase-3": {
      name: "Samruddhi Estate Phase 3",
      khNo: "Kh. No. 110",
      corridor: "Salai Dabha · Hingna – MIHAN Belt",
      location: "Mouza Salaidabha, Tah. Hingna, Dist. Nagpur",
      resRate: 1700,
      comRate: 1750,
      heroImage: "/src/assets/images/hero_nagpur_estate_1795287010697.jpg",
      sanctionRef: "NMRDA Sanctioned Agreement Dt. 02/07/2026 · Kh. No. 110",
      frontage: "12.00 m Wide Spine Road & 9.00 m Internal Avenues",
      faqQ: "What are the Residential and Commercial plot rates at Samruddhi Estate Phase 3?",
      faqA: "Samruddhi Estate Phase 3 in Salai Dabha (Kh. No. 110) starts at ₹1,700/sq.ft* for Residential plots and ₹1,750/sq.ft* for Commercial spine-road plots, subject to written cost sheet confirmation.",
    },
    "samruddhi-estate-phase-4": {
      name: "Samruddhi Estate Phase 4",
      khNo: "Kh. No. 111",
      corridor: "Salai Dabha · 18 m D.P. Road Frontage",
      location: "Mouza Salaidabha, Tah. Hingna, Dist. Nagpur",
      resRate: 1700,
      comRate: 1750,
      heroImage: "/src/assets/images/project_sajal_wardha_1795287010087.jpg",
      sanctionRef: "NMRDA Proposed Residential Layout Plan · Kh. No. 111",
      frontage: "18.00 m Wide D.P. Road & 9.00 m Internal Avenues",
      faqQ: "What is the starting price and road frontage at Samruddhi Estate Phase 4?",
      faqA: "Samruddhi Estate Phase 4 in Salai Dabha (Kh. No. 111) sits on an 18.00 m Wide D.P. Road with Residential plots starting at ₹1,700/sq.ft* and Commercial plots starting at ₹1,750/sq.ft*.",
    },
    "bhagirath-nagari": {
      name: "Bhagirathi Nagar - 4",
      khNo: "Sr. No. 87/1",
      corridor: "Mouza Kanholi · Hingna – Nagpur",
      location: "Sr. No. 87/1, P.H. No. 48, Mouza Kanholi, Tah. Hingna, Nagpur",
      resRate: 1990,
      comRate: 2050,
      heroImage: "/src/assets/images/project_samruddhi_butibori_1795287027780.jpg",
      sanctionRef: "NMRDA Digitally Signed File Ref: NMRDA-26-36337",
      frontage: "12.00 m Wide Spine Roads & 9.00 m Internal Grid",
      faqQ: "What are the plot rates and NMRDA reference for Bhagirathi Nagar - 4 in Nagpur?",
      faqA: "Bhagirathi Nagar - 4 in Mouza Kanholi (NMRDA-26-36337, 15.69 acres, 253 plots) starts at ₹1,990/sq.ft* for Residential plots and ₹2,050/sq.ft* for 12 m road Commercial plots.",
    },
    "ananta-akasha": {
      name: "Ananta & Akasha",
      khNo: "Kh. No. 64 & 64/2",
      corridor: "Hingna · 30 m Boulevard Frontage",
      location: "Kh. No. 64 & 64/2, Adj. Sanctioned NATP Kh. No. 66/2, Nagpur",
      resRate: 2650,
      heroImage: "/src/assets/images/project_ananta_hingna_1795287009980.jpg",
      sanctionRef: "Adj. Sanctioned NATP Kh. No. 66/2 · 30 m Boulevard",
      frontage: "30.00 m Wide South Road & 18.00 m Central Boulevard",
      faqQ: "How many plots remain open at Ananta & Akasha on Hingna 30m Boulevard?",
      faqA: "Ananta Wing (28 plots) is fully allotted; 7 un-circled marquee plots in Akasha Wing (Plots 1, 4, 5, 17, 22, 23 & 26) remain open for enquiry starting at ₹2,650/sq.ft*.",
    },
  };

  function escapeHtmlAttr(val: string): string {
    return val
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function resolveRouteSeo(reqPath: string, baseUrl: string): RouteSeoMeta {
    const clean = reqPath.replace(/\/$/, "") || "/";
    const thankYouMatch = clean.match(/^\/projects\/([^/]+)\/thank-you$/);
    const projectMatch = clean.match(/^\/projects\/([^/]+)$/);

    if (clean === "/privacy-policy") {
      return {
        title: "Privacy Policy — Realtor Estates | Property in Nagpur",
        description:
          "Read Realtor Estates' Privacy Policy covering GDPR & India DPDP Act 2023 compliance, cookie controls, Google Maps notice, and zero data resale for Nagpur plot enquiries.",
        robots: "index, follow",
        canonicalPath: "/privacy-policy",
        ogImagePath: "/src/assets/images/hero_nagpur_estate_1795287010697.jpg",
      };
    }

    if (clean === "/terms-and-conditions") {
      return {
        title: "Terms & Conditions — Realtor Estates | Property in Nagpur",
        description:
          "Terms & Conditions, Pricing & Loan Calculator Disclaimers, and NMRDA/MahaRERA due-diligence guidelines for Realtor Estates — Property in Nagpur.",
        robots: "index, follow",
        canonicalPath: "/terms-and-conditions",
        ogImagePath: "/src/assets/images/hero_nagpur_estate_1795287010697.jpg",
      };
    }

    if (thankYouMatch) {
      const proj = PROJECT_SEO_MAP[thankYouMatch[1]] ?? PROJECT_SEO_MAP["samruddhi-estate-phase-3"];
      return {
        title: `Confirmed — ${proj.name} | Realtor Estates`,
        description: `Your ${proj.name} enquiry is registered with Realtor Estates — Property in Nagpur.`,
        robots: "noindex, nofollow",
        canonicalPath: clean,
        ogImagePath: proj.heroImage,
      };
    }

    if (projectMatch && PROJECT_SEO_MAP[projectMatch[1]]) {
      const slug = projectMatch[1];
      const proj = PROJECT_SEO_MAP[slug];
      const priceText = proj.comRate
        ? `Res. ₹${proj.resRate.toLocaleString("en-IN")} / Com. ₹${proj.comRate.toLocaleString("en-IN")} sq.ft*`
        : `From ₹${proj.resRate.toLocaleString("en-IN")}/sq.ft*`;
      const pageUrl = `${baseUrl}/projects/${slug}`;

      return {
        title: `${proj.name} (${proj.khNo}) — 3D Plots & Satellite Map | Realtor Estates`,
        description: `Explore ${proj.name} in ${proj.location}. ${priceText}. ${proj.frontage}. Inspect every plot in 360° 3D, view live Google Maps satellite landmarks & calculate EMI.`,
        robots: "index, follow, max-image-preview:large, max-snippet:-1",
        canonicalPath: `/projects/${slug}`,
        ogImagePath: proj.heroImage,
        jsonLd: {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "BreadcrumbList",
              itemListElement: [
                { "@type": "ListItem", position: 1, name: "Realtor Estates — Property in Nagpur", item: `${baseUrl}/` },
                { "@type": "ListItem", position: 2, name: proj.name, item: pageUrl },
              ],
            },
            {
              "@type": "RealEstateListing",
              name: `${proj.name} — ${proj.corridor}`,
              description: `${proj.sanctionRef}. ${proj.frontage}. ${priceText}`,
              url: pageUrl,
              image: `${baseUrl}${proj.heroImage}`,
              offers: proj.comRate
                ? [
                    {
                      "@type": "Offer",
                      name: `${proj.name} Residential Plot`,
                      price: String(proj.resRate),
                      priceCurrency: "INR",
                      unitText: "sq.ft",
                      availability: "https://schema.org/InStock",
                    },
                    {
                      "@type": "Offer",
                      name: `${proj.name} Commercial Plot`,
                      price: String(proj.comRate),
                      priceCurrency: "INR",
                      unitText: "sq.ft",
                      availability: "https://schema.org/InStock",
                    },
                  ]
                : {
                    "@type": "Offer",
                    name: `${proj.name} Estate Plot`,
                    price: String(proj.resRate),
                    priceCurrency: "INR",
                    unitText: "sq.ft",
                    availability: "https://schema.org/InStock",
                  },
            },
            {
              "@type": "FAQPage",
              mainEntity: [
                {
                  "@type": "Question",
                  name: proj.faqQ,
                  acceptedAnswer: { "@type": "Answer", text: proj.faqA },
                },
              ],
            },
          ],
        },
      };
    }

    return {
      title: "Realtor Estates — Property in Nagpur | NMRDA Plots",
      description:
        "Buy Your Estates, Build Your Legacy in Nagpur. Inspect NMRDA plots in 3D, 360° & Google Maps Satellite across Salai Dabha, Kanholi & Hingna — starting ₹1,700/sq.ft*.",
      robots: "index, follow, max-image-preview:large, max-snippet:-1",
      canonicalPath: "/",
      ogImagePath: "/src/assets/images/hero_nagpur_estate_1795287010697.jpg",
    };
  }

  function injectRouteSeoHtml(templateHtml: string, req: express.Request): string {
    const baseUrl =
      process.env.APP_URL?.trim().replace(/\/$/, "") ||
      `${req.protocol}://${req.get("host")}`;
    const seo = resolveRouteSeo(req.path, baseUrl);
    const canonicalUrl = `${baseUrl}${seo.canonicalPath}`;
    const ogImageUrl = `${baseUrl}${seo.ogImagePath}`;

    let html = templateHtml
      .replace(/<title>[\s\S]*?<\/title>/i, `<title>${escapeHtmlAttr(seo.title)}</title>`)
      .replace(
        /<meta\s+name="description"[\s\S]*?\/>/i,
        `<meta name="description" content="${escapeHtmlAttr(seo.description)}" />`,
      )
      .replace(
        /<meta\s+name="robots"[\s\S]*?\/>/i,
        `<meta name="robots" content="${escapeHtmlAttr(seo.robots)}" />`,
      )
      .replace(
        /<meta\s+property="og:title"[\s\S]*?\/>/i,
        `<meta property="og:title" content="${escapeHtmlAttr(seo.title)}" />`,
      )
      .replace(
        /<meta\s+property="og:description"[\s\S]*?\/>/i,
        `<meta property="og:description" content="${escapeHtmlAttr(seo.description)}" />`,
      )
      .replace(
        /<meta\s+property="og:image"[\s\S]*?\/>/i,
        `<meta property="og:image" content="${escapeHtmlAttr(ogImageUrl)}" />`,
      )
      .replace(
        /<meta\s+name="twitter:title"[\s\S]*?\/>/i,
        `<meta name="twitter:title" content="${escapeHtmlAttr(seo.title)}" />`,
      )
      .replace(
        /<meta\s+name="twitter:description"[\s\S]*?\/>/i,
        `<meta name="twitter:description" content="${escapeHtmlAttr(seo.description)}" />`,
      )
      .replace(
        /<meta\s+name="twitter:image"[\s\S]*?\/>/i,
        `<meta name="twitter:image" content="${escapeHtmlAttr(ogImageUrl)}" />`,
      );

    const extraHeadTags = [
      `<link rel="canonical" href="${escapeHtmlAttr(canonicalUrl)}" />`,
      `<meta property="og:url" content="${escapeHtmlAttr(canonicalUrl)}" />`,
      seo.jsonLd
        ? `<script type="application/ld+json">${JSON.stringify(seo.jsonLd).replace(/</g, "\\u003c")}</script>`
        : "",
    ]
      .filter(Boolean)
      .join("\n    ");

    html = html.replace("</head>", `    ${extraHeadTags}\n  </head>`);
    return html;
  }

  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "custom",
    });
    app.use(vite.middlewares);

    app.get("*", async (req, res, next) => {
      // Skip non-HTML asset/API paths
      if (req.path.startsWith("/api/") || /\.[a-zA-Z0-9]{2,8}$/.test(req.path)) {
        next();
        return;
      }
      try {
        const indexPath = path.resolve(process.cwd(), "index.html");
        const rawTemplate = fs.readFileSync(indexPath, "utf8");
        const transformed = await vite.transformIndexHtml(req.originalUrl, rawTemplate);
        const finalHtml = injectRouteSeoHtml(transformed, req);
        res.status(200).set({ "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" }).end(finalHtml);
      } catch (err) {
        vite.ssrFixStacktrace(err as Error);
        next(err);
      }
    });
  } else {
    const distDir = path.resolve(process.cwd(), "dist");
    const distIndexHtml = fs.readFileSync(path.resolve(distDir, "index.html"), "utf8");

    app.use(
      "/assets",
      express.static(path.resolve(distDir, "assets"), {
        maxAge: "365d",
        immutable: true,
      }),
    );
    app.use(
      express.static(distDir, {
        index: false,
        maxAge: "1d",
        setHeaders: (res, filePath) => {
          if (filePath.endsWith(".html")) {
            res.setHeader("Cache-Control", "no-cache");
          }
        },
      }),
    );
    app.get("*", (req, res) => {
      const finalHtml = injectRouteSeoHtml(distIndexHtml, req);
      res.status(200).set({ "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" }).send(finalHtml);
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Realtor Estates server listening on http://localhost:${PORT}`);
  });
}

startServer();

