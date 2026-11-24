import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  APIProvider,
  APILoadingStatus,
  AdvancedMarker,
  AltitudeMode,
  InfoWindow,
  Map as GoogleMap,
  Map3D,
  Marker3D,
  Pin,
  useApiLoadingStatus,
  useMap,
  useMapsLibrary,
  type Map3DCameraChangedEvent,
  type Map3DRef,
} from "@vis.gl/react-google-maps";
import {
  ArrowUpRight,
  Building2,
  Compass,
  ExternalLink,
  Globe2,
  GraduationCap,
  Hospital,
  Layers,
  Loader2,
  MapPin,
  Navigation,
  Orbit,
  Route,
  Satellite,
} from "lucide-react";
import { PROJECTS, summarizeLayout, type EstateProject } from "../data/projects";
import { formatINR } from "../lib/finance";

const GOOGLE_MAPS_API_KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined)?.trim() || "";
const NAGPUR_DEFAULT_CENTER = { lat: 21.1458, lng: 79.0882 };

type CorridorQueryConfig = {
  slug: string;
  queries: string[];
  shortLabel: string;
  exactSiteNote: string;
};

// Exact user-verified site addresses resolved live via Google Maps Places API (New) — zero hardcoded coordinates
const CORRIDOR_SEARCH_QUERIES: Record<string, CorridorQueryConfig> = {
  "samruddhi-estate-phase-3": {
    slug: "samruddhi-estate-phase-3",
    queries: [
      "Salai Dabha, Hingna, Nagpur, Maharashtra 441108",
      "Salai Dabha, Nagpur, Maharashtra, India",
    ],
    shortLabel: "Phase 3 · Salai Dabha",
    exactSiteNote: "Mouza Salai Dabha, Tah. Hingna, Nagpur",
  },
  "samruddhi-estate-phase-4": {
    slug: "samruddhi-estate-phase-4",
    queries: [
      "Salai Dabha Road, Hingna, Nagpur, Maharashtra 441108",
      "Salai Dabha, Hingna, Nagpur, Maharashtra, India",
    ],
    shortLabel: "Phase 4 · Salai Dabha",
    exactSiteNote: "18 m D.P. Road, Mouza Salai Dabha, Nagpur",
  },
  "bhagirath-nagari": {
    slug: "bhagirath-nagari",
    queries: [
      "Gurudev zagekar plot, X2C5+96, Kanholi, Maharashtra 441108",
      "X2C5+96, Kanholi, Maharashtra 441108",
      "Kanholi, Hingna, Nagpur, Maharashtra 441108",
    ],
    shortLabel: "Bhagirathi Nagar - 4 · Kanholi",
    exactSiteNote: "Directly opposite Gurudev Zagekar Plot, X2C5+96, Kanholi, Nagpur 441108",
  },
  "ananta-akasha": {
    slug: "ananta-akasha",
    queries: [
      "Satish Electricals branch 2, X2RH+PF, Dongargaon, Maharashtra 441108",
      "X2RH+PF, Dongargaon, Maharashtra 441108",
      "Dongargaon, Hingna, Nagpur, Maharashtra 441108",
    ],
    shortLabel: "Ananta & Akasha · Dongargaon",
    exactSiteNote: "Exact Site · Satish Electricals Branch 2, X2RH+PF, Dongargaon, Nagpur 441108",
  },
};

type LandmarkCategoryId = "transit" | "healthcare" | "education" | "employment";

const LANDMARK_CATEGORIES: Array<{
  id: LandmarkCategoryId;
  label: string;
  query: string;
}> = [
  { id: "transit", label: "Expressway & Airport", query: "Samruddhi Mahamarg Nagpur Airport Metro Station" },
  { id: "healthcare", label: "Hospitals & AIIMS", query: "AIIMS Hospital multispeciality hospital Hingna Nagpur" },
  { id: "education", label: "Schools & Colleges", query: "School Engineering College IIM Hingna Nagpur" },
  { id: "employment", label: "MIHAN & Industrial", query: "MIHAN SEZ Hingna MIDC Butibori Nagpur" },
];

type ResolvedPlacePin = {
  id: string;
  name: string;
  address: string;
  exactSiteNote?: string;
  location: { lat: number; lng: number };
  googleMapsURI?: string;
  projectSlug?: string;
};

function isQuotaError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return (
    msg.includes("RESOURCE_EXHAUSTED") ||
    msg.includes("OVER_QUERY_LIMIT") ||
    msg.includes("QuotaExceeded") ||
    msg.includes("429")
  );
}

type LatLngLike =
  | { lat: number; lng: number }
  | { lat: () => number; lng: () => number }
  | null
  | undefined;

function toLatLngLiteral(loc: LatLngLike): { lat: number; lng: number } | null {
  if (!loc) return null;
  const lat = typeof loc.lat === "function" ? loc.lat() : loc.lat;
  const lng = typeof loc.lng === "function" ? loc.lng() : loc.lng;
  if (typeof lat !== "number" || typeof lng !== "number" || Number.isNaN(lat) || Number.isNaN(lng)) {
    return null;
  }
  return { lat, lng };
}

type InnerMapProps = {
  project?: EstateProject;
  mode: "project" | "overview";
  onNavigate?: (path: string) => void;
  onSwitchTo3D?: () => void;
};

type CameraPreset = "orbital" | "approach" | "topdown";

function NagpurMapCanvas({ project, mode, onNavigate, onSwitchTo3D }: InnerMapProps) {
  const map2D = useMap("nagpur-2d-map");
  const map3dRef = useRef<Map3DRef | null>(null);
  const coreLib = useMapsLibrary("core");
  const placesLib = useMapsLibrary("places");
  const loadingStatus = useApiLoadingStatus();

  const [perspective, setPerspective] = useState<"3d-satellite" | "2d-satellite">("3d-satellite");
  const [threeDUnavailable, setThreeDUnavailable] = useState(false);
  const [mapTypeId, setMapTypeId] = useState<"hybrid" | "roadmap" | "terrain">("hybrid");
  const [corridorPins, setCorridorPins] = useState<Record<string, ResolvedPlacePin>>({});
  const [landmarkPins, setLandmarkPins] = useState<ResolvedPlacePin[]>([]);
  const [activeCategory, setActiveCategory] = useState<LandmarkCategoryId | null>(null);
  const [loadingCorridors, setLoadingCorridors] = useState(false);
  const [loadingLandmarks, setLoadingLandmarks] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [selectedPin, setSelectedPin] = useState<ResolvedPlacePin | null>(null);
  const [activeSiteSlug, setActiveSiteSlug] = useState<string>(project?.slug ?? "ananta-akasha");
  const [cameraPreset, setCameraPreset] = useState<CameraPreset>("orbital");
  const [isOrbiting, setIsOrbiting] = useState(false);

  // Live 3D Satellite camera telemetry readout (God's Eye aerial HUD)
  const [telemetry, setTelemetry] = useState({ range: 1650, tilt: 62, heading: 28 });

  const targetProjects = useMemo(
    () => (mode === "project" && project ? [project] : PROJECTS),
    [mode, project],
  );

  // Resolve real site locations dynamically via Places API (New) — works across both 3D & 2D views
  useEffect(() => {
    if (!placesLib) return;
    let cancelled = false;

    const resolveCorridors = async () => {
      setLoadingCorridors(true);
      setStatusMessage(null);
      const nextPins: Record<string, ResolvedPlacePin> = {};

      for (const proj of targetProjects) {
        const cfg = CORRIDOR_SEARCH_QUERIES[proj.slug] ?? {
          slug: proj.slug,
          queries: [`${proj.mouza}, Nagpur, Maharashtra, India`],
          shortLabel: proj.name,
          exactSiteNote: proj.location,
        };

        for (const query of cfg.queries) {
          try {
            const response = await placesLib.Place.searchByText({
              textQuery: query,
              fields: ["id", "displayName", "formattedAddress", "location", "googleMapsURI"],
              region: "IN",
              maxResultCount: 1,
            });
            const first = response?.places?.[0];
            const pt = toLatLngLiteral(first?.location);
            if (first && pt) {
              // Keep Phase 3 & Phase 4 pins visually separated if they resolve to the same village center;
              // place Bhagirathi Nagar - 4 directly opposite (East side across the road from) Gurudev Zagekar Plot.
              let adjustedPt = pt;
              if (
                proj.slug === "samruddhi-estate-phase-4" &&
                nextPins["samruddhi-estate-phase-3"] &&
                Math.abs(nextPins["samruddhi-estate-phase-3"].location.lat - pt.lat) < 0.0004 &&
                Math.abs(nextPins["samruddhi-estate-phase-3"].location.lng - pt.lng) < 0.0004
              ) {
                adjustedPt = { lat: pt.lat + 0.0022, lng: pt.lng + 0.0018 };
              } else if (proj.slug === "bhagirath-nagari") {
                adjustedPt = { lat: pt.lat + 0.00025, lng: pt.lng + 0.00145 };
              }

              const mapsUri =
                proj.slug === "bhagirath-nagari"
                  ? `https://www.google.com/maps/search/?api=1&query=${adjustedPt.lat.toFixed(6)},${adjustedPt.lng.toFixed(6)}`
                  : first.googleMapsURI || undefined;

              nextPins[proj.slug] = {
                id: first.id || proj.slug,
                name: proj.name,
                address: first.formattedAddress || proj.location,
                exactSiteNote: cfg.exactSiteNote,
                location: adjustedPt,
                googleMapsURI: mapsUri,
                projectSlug: proj.slug,
              };
              break;
            }
          } catch (err) {
            if (isQuotaError(err)) {
              window.dispatchEvent(new CustomEvent("gmp-quota-exceeded"));
              if (!cancelled) {
                setStatusMessage("Live place search paused because the daily map quota was reached.");
              }
              break;
            }
          }
        }
      }

      if (!cancelled) {
        setCorridorPins(nextPins);
        setLoadingCorridors(false);
        const primarySlug = project?.slug ?? "ananta-akasha";
        const primary = nextPins[primarySlug] ?? Object.values(nextPins)[0];
        if (primary) {
          setActiveSiteSlug(primary.projectSlug ?? primarySlug);
          setSelectedPin(primary);
        }
      }
    };

    resolveCorridors();
    return () => {
      cancelled = true;
    };
  }, [placesLib, targetProjects, project?.slug]);

  // Fit 2D map bounds whenever 2D map becomes active & corridor pins are ready
  useEffect(() => {
    if (perspective !== "2d-satellite" || !map2D || !coreLib) return;
    const pins = Object.values(corridorPins);
    if (pins.length === 0) return;

    if (pins.length === 1) {
      map2D.panTo(pins[0].location);
      map2D.setZoom(16);
    } else {
      const bounds = new coreLib.LatLngBounds();
      pins.forEach((p) => bounds.extend(p.location));
      map2D.fitBounds(bounds, { top: 64, right: 48, bottom: 64, left: 48 });
    }
  }, [perspective, map2D, coreLib, corridorPins]);

  // Fly 3D God's Eye camera smoothly to the active Nagpur estate once resolved
  const fly3DToPin = useCallback(
    (pin: ResolvedPlacePin, preset: CameraPreset = cameraPreset) => {
      const ref = map3dRef.current;
      if (!ref) return;
      setIsOrbiting(false);

      const cameraConfig =
        preset === "topdown"
          ? { range: 1150, tilt: 0, heading: 0 }
          : preset === "approach"
            ? { range: 780, tilt: 70, heading: 42 }
            : { range: 1450, tilt: 60, heading: 25 };

      try {
        ref.flyCameraTo({
          endCamera: {
            center: { lat: pin.location.lat, lng: pin.location.lng, altitude: 310 },
            ...cameraConfig,
            roll: 0,
          },
          durationMillis: 1400,
        });
      } catch {
        // Ignore if Map3D element is still warming up
      }
    },
    [cameraPreset],
  );

  // Auto-focus 3D camera onto the primary resolved site
  useEffect(() => {
    if (perspective !== "3d-satellite") return;
    const targetPin = corridorPins[activeSiteSlug] ?? Object.values(corridorPins)[0];
    if (!targetPin) return;
    const t = window.setTimeout(() => fly3DToPin(targetPin, cameraPreset), 260);
    return () => window.clearTimeout(t);
  }, [perspective, corridorPins, activeSiteSlug, cameraPreset, fly3DToPin]);

  const handleOrbit360 = () => {
    const ref = map3dRef.current;
    const targetPin = corridorPins[activeSiteSlug] ?? Object.values(corridorPins)[0];
    if (!ref || !targetPin) return;

    if (isOrbiting) {
      setIsOrbiting(false);
      fly3DToPin(targetPin, cameraPreset);
      return;
    }

    setIsOrbiting(true);
    try {
      ref.flyCameraAround({
        camera: {
          center: { lat: targetPin.location.lat, lng: targetPin.location.lng, altitude: 310 },
          range: 1250,
          tilt: 62,
          heading: telemetry.heading,
          roll: 0,
        },
        durationMillis: 18000,
        rounds: 1,
      });
    } catch {
      setIsOrbiting(false);
    }
  };

  const handleCameraChanged3D = useCallback((ev: Map3DCameraChangedEvent) => {
    const detail = ev.detail;
    setTelemetry((prev) => ({
      range: typeof detail.range === "number" ? Math.round(detail.range) : prev.range,
      tilt: typeof detail.tilt === "number" ? Math.round(detail.tilt) : prev.tilt,
      heading: typeof detail.heading === "number" ? Math.round(((detail.heading % 360) + 360) % 360) : prev.heading,
    }));
  }, []);

  const handleFocusSite = (projSlug: string) => {
    setActiveSiteSlug(projSlug);
    const pin = corridorPins[projSlug];
    if (!pin) return;
    setSelectedPin(pin);
    if (perspective === "3d-satellite") {
      fly3DToPin(pin, cameraPreset);
    } else if (map2D) {
      map2D.panTo(pin.location);
      map2D.setZoom(16);
    }
  };

  const handleLoadLandmarks = useCallback(
    async (categoryId: LandmarkCategoryId) => {
      if (!placesLib || !coreLib) return;

      // Switch smoothly to 2D satellite when inspecting nearby Places pins
      if (perspective === "3d-satellite") {
        setPerspective("2d-satellite");
      }

      if (activeCategory === categoryId) {
        setActiveCategory(null);
        setLandmarkPins([]);
        setStatusMessage(null);
        return;
      }

      const category = LANDMARK_CATEGORIES.find((c) => c.id === categoryId);
      if (!category) return;

      setActiveCategory(categoryId);
      setLoadingLandmarks(true);
      setStatusMessage(null);

      const primaryPin = corridorPins[activeSiteSlug] ?? Object.values(corridorPins)[0];
      const biasCenter = primaryPin?.location ?? NAGPUR_DEFAULT_CENTER;

      try {
        const response = await placesLib.Place.searchByText({
          textQuery: category.query,
          fields: ["id", "displayName", "formattedAddress", "location", "googleMapsURI"],
          region: "IN",
          locationBias: {
            center: biasCenter,
            radius: 15000,
          },
          maxResultCount: 6,
        });

        const resolved: ResolvedPlacePin[] = [];
        const bounds = new coreLib.LatLngBounds();
        if (primaryPin) bounds.extend(primaryPin.location);

        for (const p of response?.places ?? []) {
          const loc = toLatLngLiteral(p?.location);
          if (p?.id && loc) {
            resolved.push({
              id: p.id,
              name: p.displayName || "Nagpur Landmark",
              address: p.formattedAddress || "Nagpur, Maharashtra",
              location: loc,
              googleMapsURI: p.googleMapsURI || undefined,
            });
            bounds.extend(loc);
          }
        }

        setLandmarkPins(resolved);
        if (resolved.length === 0) {
          setStatusMessage("No nearby landmarks found for this filter in the visible area.");
        } else if (map2D) {
          map2D.fitBounds(bounds, { top: 60, right: 48, bottom: 60, left: 48 });
        }
      } catch (err) {
        if (isQuotaError(err)) {
          window.dispatchEvent(new CustomEvent("gmp-quota-exceeded"));
          setStatusMessage("Nearby search unavailable — daily Google Maps quota reached.");
        } else {
          setStatusMessage("Could not load nearby landmarks right now.");
        }
        setLandmarkPins([]);
      } finally {
        setLoadingLandmarks(false);
      }
    },
    [placesLib, coreLib, map2D, perspective, activeCategory, corridorPins, activeSiteSlug],
  );

  const activePin = corridorPins[activeSiteSlug] ?? Object.values(corridorPins)[0] ?? null;
  const selectedProjectObj = selectedPin?.projectSlug
    ? PROJECTS.find((p) => p.slug === selectedPin.projectSlug)
    : null;

  if (loadingStatus === APILoadingStatus.FAILED || loadingStatus === APILoadingStatus.AUTH_FAILURE) {
    return (
      <div className="flex min-h-[380px] flex-col items-center justify-center rounded-3xl border border-white/10 bg-[#0c1612] p-8 text-center text-sm text-[#B7C4BB]">
        <MapPin className="h-7 w-7 text-[#D4AF5F]" aria-hidden="true" />
        <p className="mt-3 font-semibold text-[#F4F1E7]">Live Satellite View Unavailable</p>
        <p className="mt-1 max-w-md text-xs text-[#98A89D]">
          Please switch back to the 3D Parcel Map or check your connection.
        </p>
        {onSwitchTo3D ? (
          <button
            type="button"
            onClick={onSwitchTo3D}
            className="mt-4 inline-flex min-h-[42px] items-center gap-2 rounded-xl bg-[#D4AF5F] px-4 py-2 text-xs font-semibold text-[#080C0A]"
          >
            <Compass className="h-3.5 w-3.5" />
            <span>Back to 3D Parcel Map</span>
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Apple-Clean Top Control Bar: Live 3D God's Eye vs 2D Satellite & Nearby Places */}
      <div className="flex flex-col gap-3 rounded-2xl border border-black/[0.08] bg-white p-3.5 dark:border-white/[0.09] dark:bg-[#0D1612] lg:flex-row lg:items-center lg:justify-between">
        {/* Primary Camera Mode Switcher */}
        <div
          role="tablist"
          aria-label="Satellite camera mode"
          className="inline-flex self-start rounded-xl border border-black/10 bg-[#F4F3EE] p-1 dark:border-white/10 dark:bg-[#080F0C]"
        >
          <button
            type="button"
            role="tab"
            aria-selected={perspective === "3d-satellite"}
            disabled={threeDUnavailable}
            onClick={() => setPerspective("3d-satellite")}
            className={`inline-flex min-h-[36px] items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-colors ${
              perspective === "3d-satellite"
                ? "bg-[#10281E] text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                : "text-[#4D5C53] hover:text-[#102018] disabled:opacity-45 dark:text-[#9AA99E] dark:hover:text-white"
            }`}
          >
            <Satellite className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>3D Live Satellite</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={perspective === "2d-satellite"}
            onClick={() => setPerspective("2d-satellite")}
            className={`inline-flex min-h-[36px] items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-colors ${
              perspective === "2d-satellite"
                ? "bg-[#10281E] text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                : "text-[#4D5C53] hover:text-[#102018] dark:text-[#9AA99E] dark:hover:text-white"
            }`}
          >
            <Globe2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>2D Map &amp; Nearby Places</span>
          </button>
        </div>

        {/* Context Controls: 3D Aerial Angles OR 2D Landmark Filters */}
        {perspective === "3d-satellite" ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {(
              [
                { id: "orbital", label: "Orbital 60°" },
                { id: "approach", label: "Low Flyover" },
                { id: "topdown", label: "Top-Down" },
              ] as const
            ).map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => {
                  setCameraPreset(preset.id);
                  if (activePin) fly3DToPin(activePin, preset.id);
                }}
                className={`min-h-[36px] rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                  cameraPreset === preset.id && !isOrbiting
                    ? "border-[#8C6D2E] bg-[#10281E]/10 text-[#102018] dark:border-[#D4AF5F] dark:bg-[#D4AF5F]/15 dark:text-[#F4F1E7]"
                    : "border-black/10 bg-[#F8F7F2] text-[#4A5950] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#13201A] dark:text-[#C5D1C8] dark:hover:border-[#D4AF5F]"
                }`}
              >
                {preset.label}
              </button>
            ))}
            <button
              type="button"
              onClick={handleOrbit360}
              className={`inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border px-3.5 py-1.5 text-xs font-semibold transition-colors ${
                isOrbiting
                  ? "border-[#D4AF5F] bg-[#D4AF5F] text-[#080C0A]"
                  : "border-[#10281E]/20 bg-[#10281E] text-[#F6F5F0] hover:bg-[#18392b] dark:border-[#D4AF5F]/40 dark:bg-[#15251E] dark:text-[#E5C77B]"
              }`}
            >
              <Orbit className={`h-3.5 w-3.5 ${isOrbiting ? "animate-spin" : ""}`} aria-hidden="true" />
              <span>{isOrbiting ? "Stop 360° Orbit" : "360° Aerial Orbit"}</span>
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 inline-flex items-center gap-1 text-xs font-medium text-[#7C622B] dark:text-[#D4AF5F]">
              <Navigation className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Nearby:</span>
            </span>
            {LANDMARK_CATEGORIES.map((cat) => {
              const active = activeCategory === cat.id;
              const Icon =
                cat.id === "transit"
                  ? Route
                  : cat.id === "healthcare"
                    ? Hospital
                    : cat.id === "education"
                      ? GraduationCap
                      : Building2;
              return (
                <button
                  key={cat.id}
                  type="button"
                  disabled={loadingLandmarks}
                  onClick={() => handleLoadLandmarks(cat.id)}
                  className={`inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? "border-[#D4AF5F] bg-[#D4AF5F] text-[#080C0A]"
                      : "border-black/10 bg-[#F8F7F2] text-[#24342B] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#13201A] dark:text-[#DDE4DE] dark:hover:border-[#D4AF5F]"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>{cat.label}</span>
                </button>
              );
            })}
            <div
              role="group"
              aria-label="2D map layer"
              className="ml-auto flex items-center gap-1 rounded-lg border border-black/10 bg-[#F4F3EE] p-0.5 dark:border-white/10 dark:bg-[#080F0C]"
            >
              <Layers className="mx-1 h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
              {(
                [
                  { id: "hybrid", label: "Satellite" },
                  { id: "roadmap", label: "Roads" },
                ] as const
              ).map((layer) => (
                <button
                  key={layer.id}
                  type="button"
                  onClick={() => setMapTypeId(layer.id)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                    mapTypeId === layer.id
                      ? "bg-[#10281E] text-[#F6F5F0] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                      : "text-[#516057] dark:text-[#9AA99E]"
                  }`}
                >
                  {layer.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Quick Site Focus Strip in Overview Mode */}
      {mode === "overview" ? (
        <div className="flex flex-wrap items-center gap-2">
          {PROJECTS.map((proj) => {
            const isSelected = activeSiteSlug === proj.slug;
            return (
              <button
                key={proj.slug}
                type="button"
                onClick={() => handleFocusSite(proj.slug)}
                className={`inline-flex min-h-[38px] items-center gap-2 rounded-xl border px-3.5 py-1.5 text-xs font-medium transition-colors ${
                  isSelected
                    ? "border-[#10281E] bg-[#10281E] text-[#F6F5F0] dark:border-[#D4AF5F] dark:bg-[#D4AF5F] dark:text-[#080C0A]"
                    : "border-black/10 bg-white text-[#16261E] hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#ECE8DD] dark:hover:border-[#D4AF5F]"
                }`}
              >
                <MapPin className="h-3.5 w-3.5 shrink-0 opacity-80" aria-hidden="true" />
                <span>{CORRIDOR_SEARCH_QUERIES[proj.slug]?.shortLabel ?? proj.name}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      {/* Explicit-Height Live Satellite Viewport (Zero 0x0 Collapse) */}
      <div className="relative h-[460px] w-full overflow-hidden rounded-3xl border border-black/15 bg-[#09130F] sm:h-[560px] dark:border-[#D4AF5F]/25">
        {perspective === "3d-satellite" && !threeDUnavailable ? (
          <>
            <Map3D
              ref={map3dRef}
              id="nagpur-gods-eye-3d"
              internalUsageAttributionIds={["gmp_mcp_codeassist_v1_aistudio"]}
              mode="HYBRID"
              defaultCenter={{
                lat: activePin?.location.lat ?? NAGPUR_DEFAULT_CENTER.lat,
                lng: activePin?.location.lng ?? NAGPUR_DEFAULT_CENTER.lng,
                altitude: 310,
              }}
              defaultRange={1650}
              defaultTilt={60}
              defaultHeading={25}
              defaultRoll={0}
              onCameraChanged={handleCameraChanged3D}
              onError={() => {
                setThreeDUnavailable(true);
                setPerspective("2d-satellite");
              }}
              style={{ width: "100%", height: "100%" }}
            >
              {Object.values(corridorPins).map((pin) => (
                <Marker3D
                  key={`3d-${pin.projectSlug || pin.id}`}
                  position={{ lat: pin.location.lat, lng: pin.location.lng, altitude: 32 }}
                  altitudeMode={AltitudeMode.RELATIVE_TO_GROUND}
                  extruded
                  label={pin.name}
                  onClick={() => {
                    if (pin.projectSlug) handleFocusSite(pin.projectSlug);
                  }}
                />
              ))}
            </Map3D>

            {/* Minimal God's Eye Live Camera Readout + Exact Site Card */}
            <div className="pointer-events-none absolute top-4 left-4 right-4 z-10 flex flex-wrap items-start justify-between gap-3">
              {activePin ? (
                <div className="pointer-events-auto max-w-sm rounded-2xl border border-[#D4AF5F]/30 bg-[#08120E]/92 px-4 py-3 text-xs text-[#F4F1E7] shadow-lg">
                  <p className="font-semibold text-[#E5C77B]">{activePin.name}</p>
                  <p className="mt-0.5 leading-snug text-[#D6DFD8]">
                    {activePin.exactSiteNote ?? activePin.address}
                  </p>
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    {activePin.projectSlug && onNavigate && mode === "overview" ? (
                      <button
                        type="button"
                        onClick={() => onNavigate(`/projects/${activePin.projectSlug}`)}
                        className="inline-flex items-center gap-1 rounded-lg bg-[#D4AF5F] px-3 py-1.5 text-[11px] font-semibold text-[#080C0A]"
                      >
                        <span>Open 3D Plot Map</span>
                        <ArrowUpRight className="h-3 w-3" />
                      </button>
                    ) : null}
                    {activePin.googleMapsURI ? (
                      <a
                        href={activePin.googleMapsURI}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="inline-flex items-center gap-1 rounded-lg border border-white/20 px-2.5 py-1.5 text-[11px] font-medium text-[#EFECE2] hover:border-[#D4AF5F]"
                      >
                        <span>Directions</span>
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* Clean Satellite Camera Telemetry */}
              <div className="hidden items-center gap-4 rounded-xl border border-white/15 bg-[#08120E]/90 px-3.5 py-2 font-mono-tabular text-[11px] text-[#D7E1DA] sm:flex">
                <span>ALT {telemetry.range}m</span>
                <span className="text-white/25">|</span>
                <span>TILT {telemetry.tilt}°</span>
                <span className="text-white/25">|</span>
                <span>HDG {String(telemetry.heading).padStart(3, "0")}°</span>
              </div>
            </div>
          </>
        ) : (
          <GoogleMap
            id="nagpur-2d-map"
            mapId="DEMO_MAP_ID"
            internalUsageAttributionIds={["gmp_mcp_codeassist_v1_aistudio"]}
            defaultCenter={activePin?.location ?? NAGPUR_DEFAULT_CENTER}
            defaultZoom={mode === "project" ? 16 : 12}
            mapTypeId={mapTypeId}
            gestureHandling="cooperative"
            mapTypeControl={false}
            streetViewControl={true}
            fullscreenControl={true}
            className="h-full w-full"
            style={{ width: "100%", height: "100%" }}
            onClick={() => setSelectedPin(null)}
          >
            {Object.values(corridorPins).map((pin) => (
              <AdvancedMarker
                key={`estate-${pin.projectSlug || pin.id}`}
                position={pin.location}
                title={pin.name}
                onClick={() => setSelectedPin(pin)}
              >
                <div className="flex items-center gap-1.5 rounded-xl border border-[#F2D583] bg-[#0B1712]/95 px-3 py-1.5 text-[11px] font-semibold text-[#F7F3E7] shadow-lg">
                  <MapPin className="h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
                  <span className="whitespace-nowrap">{pin.name}</span>
                </div>
              </AdvancedMarker>
            ))}

            {landmarkPins.map((lm) => (
              <AdvancedMarker
                key={`lm-${lm.id}`}
                position={lm.location}
                title={lm.name}
                onClick={() => setSelectedPin(lm)}
              >
                <Pin background="#10281E" borderColor="#D4AF5F" glyphColor="#F5D47A" scale={0.9} />
              </AdvancedMarker>
            ))}

            {selectedPin ? (
              <InfoWindow
                position={selectedPin.location}
                onCloseClick={() => setSelectedPin(null)}
                maxWidth={300}
              >
                <div className="p-1 text-[#102018]">
                  <p className="text-[11px] font-semibold text-[#8C6D2E]">
                    {selectedProjectObj ? selectedProjectObj.corridor : "Nearby Nagpur Landmark"}
                  </p>
                  <h3 className="mt-0.5 text-sm font-bold text-[#102018]">{selectedPin.name}</h3>
                  <p className="mt-1 text-xs leading-snug text-[#46554C]">
                    {selectedPin.exactSiteNote ?? selectedPin.address}
                  </p>

                  {selectedProjectObj ? (
                    <div className="mt-2 border-t border-black/10 pt-2 text-[11px]">
                      <p className="font-semibold text-[#102018]">
                        {selectedProjectObj.commercialRatePerSqFt
                          ? `Res. ${formatINR(selectedProjectObj.baseRatePerSqFt)}* · Com. ${formatINR(selectedProjectObj.commercialRatePerSqFt)}* / sq.ft`
                          : `From ${formatINR(selectedProjectObj.baseRatePerSqFt)}* / sq.ft`}
                      </p>
                      <p className="mt-0.5 text-[#516057]">
                        {summarizeLayout(selectedProjectObj.layout).available} plots open ·{" "}
                        {selectedProjectObj.frontage.split("·")[0].trim()}
                      </p>
                    </div>
                  ) : null}

                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    {selectedProjectObj && onNavigate && mode === "overview" ? (
                      <button
                        type="button"
                        onClick={() => onNavigate(`/projects/${selectedProjectObj.slug}`)}
                        className="inline-flex items-center gap-1 rounded-lg bg-[#10281E] px-2.5 py-1.5 text-[11px] font-semibold text-[#F6F5F0]"
                      >
                        <span>Open 3D Map</span>
                        <ArrowUpRight className="h-3 w-3" />
                      </button>
                    ) : null}

                    {selectedProjectObj && onSwitchTo3D && mode === "project" ? (
                      <button
                        type="button"
                        onClick={onSwitchTo3D}
                        className="inline-flex items-center gap-1 rounded-lg bg-[#10281E] px-2.5 py-1.5 text-[11px] font-semibold text-[#F6F5F0]"
                      >
                        <Compass className="h-3 w-3" />
                        <span>3D Plots</span>
                      </button>
                    ) : null}

                    {selectedPin.googleMapsURI ? (
                      <a
                        href={selectedPin.googleMapsURI}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="inline-flex items-center gap-1 rounded-lg border border-black/15 px-2.5 py-1.5 text-[11px] font-medium text-[#16261E] hover:border-[#8C6D2E]"
                      >
                        <span>Google Maps</span>
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ) : null}
                  </div>
                </div>
              </InfoWindow>
            ) : null}
          </GoogleMap>
        )}

        {/* Non-Blocking Loading Indicator */}
        {loadingCorridors || loadingLandmarks ? (
          <div className="pointer-events-none absolute bottom-4 left-4 z-10 inline-flex items-center gap-2 rounded-xl border border-[#D4AF5F]/30 bg-[#0A1410]/95 px-3.5 py-2 text-xs text-[#E5C77B]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span>
              {loadingLandmarks ? "Finding nearby places…" : "Pinpointing exact Nagpur site…"}
            </span>
          </div>
        ) : null}
      </div>

      {statusMessage ? (
        <p className="text-xs text-[#6B7970] dark:text-[#98A89D]">{statusMessage}</p>
      ) : null}

      {landmarkPins.length > 0 && perspective === "2d-satellite" ? (
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {landmarkPins.map((lm) => (
            <button
              key={lm.id}
              type="button"
              onClick={() => {
                setSelectedPin(lm);
                map2D?.panTo(lm.location);
                map2D?.setZoom(15);
              }}
              className="flex items-start justify-between gap-2 rounded-2xl border border-black/[0.08] bg-white p-3.5 text-left transition-colors hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:hover:border-[#D4AF5F]"
            >
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-[#102018] dark:text-[#F4F1E7]">
                  {lm.name}
                </p>
                <p className="mt-0.5 line-clamp-2 text-[11px] text-[#57665D] dark:text-[#95A69A]">
                  {lm.address}
                </p>
              </div>
              <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : null}

      <p className="text-xs leading-relaxed text-[#64736A] dark:text-[#8E9F93]">
        Live satellite imagery via Google Maps.{" "}
        <strong>Ananta &amp; Akasha</strong> is pinned at Satish Electricals Branch 2 (X2RH+PF,
        Dongargaon), and <strong>Bhagirathi Nagar - 4</strong> sits directly opposite Gurudev
        Zagekar Plot (X2C5+96, Kanholi). Inspect exact plot numbers and measurements on the 3D
        Master Layout.
      </p>
    </div>
  );
}

export type NagpurGoogleMapViewProps = {
  project?: EstateProject;
  mode?: "project" | "overview";
  onNavigate?: (path: string) => void;
  onSwitchTo3D?: () => void;
};

export default function NagpurGoogleMapView({
  project,
  mode = "project",
  onNavigate,
  onSwitchTo3D,
}: NagpurGoogleMapViewProps) {
  if (!GOOGLE_MAPS_API_KEY) {
    return (
      <div className="rounded-3xl border border-black/10 bg-[#ECEAE1] p-6 text-center text-xs text-[#4F5D54] dark:border-white/10 dark:bg-[#0D1612] dark:text-[#9AA99E]">
        Configure <code className="font-mono-tabular">VITE_GOOGLE_MAPS_API_KEY</code> to enable the
        live Google Maps 3D Satellite view.
      </div>
    );
  }

  return (
    <APIProvider
      apiKey={GOOGLE_MAPS_API_KEY}
      version="beta"
      language="en"
      region="IN"
      libraries={["places", "marker", "maps3d"]}
    >
      <NagpurMapCanvas
        project={project}
        mode={mode}
        onNavigate={onNavigate}
        onSwitchTo3D={onSwitchTo3D}
      />
    </APIProvider>
  );
}
