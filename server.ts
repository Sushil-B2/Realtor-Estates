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

  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distDir = path.resolve(process.cwd(), "dist");
    app.use(
      "/assets",
      express.static(path.resolve(distDir, "assets"), {
        maxAge: "365d",
        immutable: true,
      }),
    );
    app.use(
      express.static(distDir, {
        maxAge: "1d",
        setHeaders: (res, filePath) => {
          if (filePath.endsWith(".html")) {
            res.setHeader("Cache-Control", "no-cache");
          }
        },
      }),
    );
    app.get("*", (_req, res) => {
      res.setHeader("Cache-Control", "no-cache");
      res.sendFile(path.resolve(distDir, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Realtor Estates server listening on http://localhost:${PORT}`);
  });
}

startServer();
