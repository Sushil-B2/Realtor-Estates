import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import {
  Check,
  Compass,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Share2,
  X,
} from "lucide-react";
import {
  FACING_AZIMUTH,
  FACING_LABEL,
  STATUS_META,
  type EstateProject,
  type PlotCell,
} from "../../data/projects";
import { calculatePlotFinance, formatINR, formatINRCompact } from "../../lib/finance";
import { trackEvent } from "../../lib/analytics";
import {
  buildCompassMonument,
  buildCompositeTree,
  buildSoldPropertyModel,
  buildStreetLamp,
  buildSurveyPillar,
  createAsphaltTexture,
  createContactShadowTexture,
  createGroundTexture,
  createLabelSprite,
  detectSoftwareWebGL,
  scheduleAfterDraw,
} from "./proceduralAssets";
import { LoanPartnerBanks } from "../ComplianceAndBankLogos";
import { PricingDisclaimer, WhatsAppIcon } from "../PricingDisclaimer";

type Props = {
  project: EstateProject;
  plot: PlotCell;
  onClose: () => void;
  onReservePlot: (plot: PlotCell, estimatedBudget: number, loanNeeded: boolean) => void;
};

function azimuthToCardinal(deg: number): string {
  const norm = ((deg % 360) + 360) % 360;
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(norm / 45) % 8];
}

export default function PlotPopout3DModal({
  project,
  plot,
  onClose,
  onReservePlot,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [cameraAzimuth, setCameraAzimuth] = useState(0);
  const [autoRotate, setAutoRotate] = useState(true);
  const [copiedShare, setCopiedShare] = useState(false);

  // Interactive per-plot budget & loan calculator state
  const [ratePerSqFt, setRatePerSqFt] = useState(plot.ratePerSqFt);
  const [downPaymentPct, setDownPaymentPct] = useState(25);
  const [interestRate, setInterestRate] = useState(8.85);
  const [tenureYears, setTenureYears] = useState(15);
  const [womenConcession, setWomenConcession] = useState(false);

  const controlsRef = useRef<OrbitControls | null>(null);
  const autoRotateRef = useRef(true);
  autoRotateRef.current = autoRotate;

  useEffect(() => {
    setRatePerSqFt(plot.ratePerSqFt);
    trackEvent({
      type: "plot_360_open",
      page: `/projects/${project.slug}`,
      project: project.slug,
      plotNumber: plot.no,
      meta: `${plot.w}x${plot.d}m ${plot.faces}`,
    });
  }, [plot, project.slug]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const finance = useMemo(
    () =>
      calculatePlotFinance({
        areaSqFt: plot.areaSqFt,
        ratePerSqFt,
        plcPercent: plot.plcPercent,
        downPaymentPercent: downPaymentPct,
        annualInterestRate: interestRate,
        tenureYears,
        womenBuyerConcession: womenConcession,
      }),
    [plot.areaSqFt, plot.plcPercent, ratePerSqFt, downPaymentPct, interestRate, tenureYears, womenConcession],
  );

  // Mount dedicated 360-degree individual plot diorama
  useEffect(() => {
    const host = stageRef.current;
    if (!host) return;

    const isSoftwareGl = detectSoftwareWebGL();
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: !isSoftwareGl,
        powerPreference: "high-performance",
      });
    } catch {
      return;
    }

    renderer.setPixelRatio(isSoftwareGl ? 1 : Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    renderer.shadowMap.enabled = !isSoftwareGl;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x08100d, 1);

    const canvas = renderer.domElement;
    canvas.className = "block h-full w-full cursor-grab active:cursor-grabbing";
    host.appendChild(canvas);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x08100d);
    scene.fog = new THREE.FogExp2(0x08100d, 0.011);

    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    pmrem.dispose();

    const camera = new THREE.PerspectiveCamera(40, 1, 0.2, 260);
    camera.position.set(16, 13.5, 19);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = !isSoftwareGl;
    controls.dampingFactor = 0.06;
    controls.enablePan = false;
    controls.minDistance = 12;
    controls.maxDistance = 42;
    controls.minPolarAngle = 0.22;
    controls.maxPolarAngle = Math.PI / 2 - 0.08;
    controls.autoRotate = !isSoftwareGl;
    controls.autoRotateSpeed = 0.85;
    controls.target.set(0, 0.8, 0);
    controlsRef.current = controls;

    // 3-Point Studio Lighting Rig
    scene.add(new THREE.HemisphereLight(0xd9eadf, 0x16241d, 1.45));
    const keyLight = new THREE.DirectionalLight(0xffe0aa, 2.75);
    keyLight.position.set(-16, 26, 18);
    if (!isSoftwareGl) {
      keyLight.castShadow = true;
      keyLight.shadow.mapSize.set(1024, 1024);
      keyLight.shadow.camera.left = -22;
      keyLight.shadow.camera.right = 22;
      keyLight.shadow.camera.top = 22;
      keyLight.shadow.camera.bottom = -22;
      keyLight.shadow.bias = -0.0004;
      keyLight.shadow.normalBias = 0.02;
    }
    scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x88bfa4, 1.0);
    rimLight.position.set(18, 12, -20);
    scene.add(rimLight);

    // Normalize plot scale to fit hero pedestal nicely while preserving true aspect ratio
    const maxSide = Math.max(plot.w, plot.d);
    const unitScale = 12.5 / maxSide;
    const pw = plot.w * unitScale;
    const pd = plot.d * unitScale;

    // Soft contact shadow plane under the floating architectural earth plinth
    const shadowTex = createContactShadowTexture();
    const shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(pw + 12, pd + 12),
      new THREE.MeshBasicMaterial({
        map: shadowTex,
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
      }),
    );
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.position.y = -0.45;
    scene.add(shadowPlane);

    // 1. Subterranean stone & earth plinth (multi-layered architectural cross-section)
    const plinthGroup = new THREE.Group();
    const bedrock = new THREE.Mesh(
      new THREE.BoxGeometry(pw + 1.1, 0.85, pd + 1.1),
      new THREE.MeshStandardMaterial({ color: 0x202724, roughness: 0.9, metalness: 0.08 }),
    );
    bedrock.position.y = 0.05;
    bedrock.receiveShadow = true;
    plinthGroup.add(bedrock);

    const brassTrim = new THREE.Mesh(
      new THREE.BoxGeometry(pw + 1.24, 0.12, pd + 1.24),
      new THREE.MeshStandardMaterial({ color: 0xd4af5f, roughness: 0.24, metalness: 0.9 }),
    );
    brassTrim.position.y = 0.5;
    plinthGroup.add(brassTrim);

    // 2. Sculpted Land Parcel Turf / Mineral Surface
    const turfTex = createGroundTexture(renderer, "#2d593e", "#4e805f", 4);
    const parcelMesh = new THREE.Mesh(
      new THREE.BoxGeometry(pw, 0.42, pd),
      new THREE.MeshStandardMaterial({
        map: turfTex,
        color: new THREE.Color(STATUS_META[plot.status].hex).lerp(new THREE.Color(0xffffff), 0.18),
        roughness: 0.72,
        metalness: 0.05,
      }),
    );
    parcelMesh.position.y = 0.74;
    parcelMesh.castShadow = !isSoftwareGl;
    parcelMesh.receiveShadow = true;
    plinthGroup.add(parcelMesh);

    // 3. Subtle architectural villa setback wireframe outline
    const setbackW = pw * 0.72;
    const setbackD = pd * 0.68;
    const setbackPts = [
      new THREE.Vector3(-setbackW / 2, 0.98, -setbackD / 2),
      new THREE.Vector3(setbackW / 2, 0.98, -setbackD / 2),
      new THREE.Vector3(setbackW / 2, 0.98, setbackD / 2),
      new THREE.Vector3(-setbackW / 2, 0.98, setbackD / 2),
      new THREE.Vector3(-setbackW / 2, 0.98, -setbackD / 2),
    ];
    const setbackLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(setbackPts),
      new THREE.LineDashedMaterial({ color: 0xf4e3b5, dashSize: 0.4, gapSize: 0.28 }),
    );
    setbackLine.computeLineDistances();
    plinthGroup.add(setbackLine);
    scene.add(plinthGroup);

    // 3B. If parcel is SOLD OUT, mount a 3D luxury residence & "SOLD OUT · ALLOTTED" estate plaque directly on the land parcel
    if (plot.status === "sold") {
      const soldVilla = buildSoldPropertyModel(renderer, pw, pd);
      soldVilla.position.set(0, 0.95, 0);
      // Orient villa facade toward the fronting road
      if (plot.faces === "N") soldVilla.rotation.y = Math.PI;
      else if (plot.faces === "E") soldVilla.rotation.y = Math.PI / 2;
      else if (plot.faces === "W") soldVilla.rotation.y = -Math.PI / 2;
      scene.add(soldVilla);
    }

    // 4. Four composite brass-capped corner boundary survey pillars
    const corners: Array<[number, number]> = [
      [-pw / 2 + 0.25, -pd / 2 + 0.25],
      [pw / 2 - 0.25, -pd / 2 + 0.25],
      [pw / 2 - 0.25, pd / 2 - 0.25],
      [-pw / 2 + 0.25, pd / 2 - 0.25],
    ];
    corners.forEach(([cx, cz]) => {
      const pillar = buildSurveyPillar(0.82);
      pillar.position.set(cx, 0.94, cz);
      scene.add(pillar);
    });

    // 5. Fronting Road Strip oriented along the plot's exact compass frontage (N/S/E/W)
    const asphaltTex = createAsphaltTexture(renderer);
    const roadMat = new THREE.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.85,
      metalness: 0.06,
    });
    const isHorizontalRoad = plot.faces === "N" || plot.faces === "S";
    const roadMesh = new THREE.Mesh(
      new THREE.BoxGeometry(isHorizontalRoad ? pw + 5 : 3.2, 0.34, isHorizontalRoad ? 3.2 : pd + 5),
      roadMat,
    );
    const roadGap = 2.4;
    if (plot.faces === "N") roadMesh.position.set(0, 0.56, -(pd / 2 + roadGap));
    else if (plot.faces === "S") roadMesh.position.set(0, 0.56, pd / 2 + roadGap);
    else if (plot.faces === "E") roadMesh.position.set(pw / 2 + roadGap, 0.56, 0);
    else roadMesh.position.set(-(pw / 2 + roadGap), 0.56, 0);
    roadMesh.receiveShadow = true;
    scene.add(roadMesh);

    // Road frontage label (high-contrast, always legible above wide road strip) + avenue streetlamp + botanical corner tree
    const roadLabel = createLabelSprite(renderer, plot.road.toUpperCase(), 7.8, 1.48, {
      bg: "rgba(8, 18, 13, 0.96)",
      borderColor: "#F5D47A",
      textColor: "#FFF5D6",
      accent: true,
      alwaysOnTop: true,
      renderOrder: 22,
      fontSize: 48,
    });
    roadLabel.position.set(roadMesh.position.x, 2.55, roadMesh.position.z);
    scene.add(roadLabel);

    const lamp = buildStreetLamp(0.75);
    lamp.position.set(
      plot.faces === "E" ? pw / 2 + 1.0 : plot.faces === "W" ? -pw / 2 - 1.0 : pw / 2 + 0.9,
      0.56,
      plot.faces === "N" ? -pd / 2 - 1.0 : plot.faces === "S" ? pd / 2 + 1.0 : -pd / 2 + 0.8,
    );
    scene.add(lamp);

    const accentTree = buildCompositeTree(0.85, 0.5);
    accentTree.position.set(-pw / 2 + 1.3, 0.95, -pd / 2 + 1.3);
    scene.add(accentTree);

    // 6. 3D Measurement Dimension Bars (Width & Depth in meters & feet)
    const widthFt = (plot.w * 3.28084).toFixed(1);
    const depthFt = (plot.d * 3.28084).toFixed(1);
    const dimMat = new THREE.LineBasicMaterial({ color: 0xe5c77b });

    // Width bar (along X)
    const widthZ = plot.faces === "S" ? -pd / 2 - 1.6 : pd / 2 + 1.6;
    const widthLineGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-pw / 2, 1.05, widthZ),
      new THREE.Vector3(pw / 2, 1.05, widthZ),
    ]);
    scene.add(new THREE.Line(widthLineGeo, dimMat));

    const widthSprite = createLabelSprite(renderer, `${plot.w}m (${widthFt} ft)`, 4.9, 1.28, {
      bg: "rgba(9, 18, 14, 0.95)",
      borderColor: "rgba(212, 175, 95, 0.65)",
      textColor: "#FAF8F2",
      renderOrder: 14,
    });
    widthSprite.position.set(0, 1.85, widthZ);
    scene.add(widthSprite);

    // Depth bar (along Z)
    const depthX = plot.faces === "E" ? -pw / 2 - 1.7 : pw / 2 + 1.7;
    const depthLineGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(depthX, 1.05, -pd / 2),
      new THREE.Vector3(depthX, 1.05, pd / 2),
    ]);
    scene.add(new THREE.Line(depthLineGeo, dimMat));

    const depthSprite = createLabelSprite(renderer, `${plot.d}m (${depthFt} ft)`, 4.9, 1.28, {
      bg: "rgba(9, 18, 14, 0.95)",
      borderColor: "rgba(212, 175, 95, 0.65)",
      textColor: "#FAF8F2",
      renderOrder: 14,
    });
    depthSprite.position.set(depthX, 1.85, 0);
    scene.add(depthSprite);

    // 7. Full 360° Engraved Brass Compass Ring around the Plot Pedestal
    const compassRadius = Math.max(pw, pd) * 0.68 + 2.4;
    const compassRing = buildCompassMonument(compassRadius);
    compassRing.position.set(0, -0.1, 0);
    scene.add(compassRing);

    const cardinals: Array<[string, number, number, boolean]> = [
      ["N", 0, -compassRadius - 1.15, true],
      ["S", 0, compassRadius + 1.15, false],
      ["E", compassRadius + 1.15, 0, false],
      ["W", -compassRadius - 1.15, 0, false],
    ];
    cardinals.forEach(([txt, x, z, isNorth]) => {
      const sp = createLabelSprite(renderer, txt, 2.3, 1.35, { accent: isNorth });
      sp.position.set(x, 0.75, z);
      scene.add(sp);
    });

    if (!isSoftwareGl) {
      renderer.compile(scene, camera);
    }

    const resize = () => {
      const w = Math.max(host.clientWidth, 1);
      const h = Math.max(host.clientHeight, 1);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    let frameId = 0;
    let disposed = false;
    let lastAzimuthRounded = -1;

    const tick = () => {
      if (disposed) return;
      controls.autoRotate = autoRotateRef.current && !isSoftwareGl;
      controls.update();

      // Compute camera azimuth relative to True North (-Z)
      const angleRad = Math.atan2(camera.position.x, camera.position.z);
      const deg = Math.round(((THREE.MathUtils.radToDeg(angleRad) % 360) + 360) % 360);
      if (deg !== lastAzimuthRounded) {
        lastAzimuthRounded = deg;
        setCameraAzimuth(deg);
      }

      renderer.render(scene, camera);
      if (isSoftwareGl) {
        scheduleAfterDraw(renderer, tick, (id) => {
          frameId = id;
        });
      } else {
        frameId = window.requestAnimationFrame(tick);
      }
    };
    frameId = window.requestAnimationFrame(tick);

    return () => {
      disposed = true;
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(frameId);
      ro.disconnect();
      controls.dispose();
      envTex.dispose();
      turfTex.dispose();
      asphaltTex.dispose();
      shadowTex.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.Line || obj instanceof THREE.Sprite) {
          obj.geometry.dispose();
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach((m) => {
            if ("map" in m && m.map instanceof THREE.Texture) m.map.dispose();
            m.dispose();
          });
        }
      });
      renderer.dispose();
      canvas.remove();
    };
  }, [plot]);

  const handleShare = async () => {
    const shareUrl = `${window.location.origin}/projects/${project.slug}?plot=${encodeURIComponent(plot.no)}`;
    const shareText = [
      `Realtor Estates — ${project.name} (${project.corridor}, Nagpur)`,
      `Plot No. ${plot.no} · ${STATUS_META[plot.status].label}`,
      `Measurements: ${plot.w}m × ${plot.d}m (${plot.areaSqFt.toLocaleString("en-IN")} sq.ft / ${plot.areaSqM} sq.m)`,
      `Orientation: ${FACING_LABEL[plot.faces]} on ${plot.road}`,
      `Indicative All-Inclusive Estimate*: ${formatINRCompact(finance.totalAcquisitionCost)} (Est. EMI ~${formatINR(finance.monthlyEmi)}/mo)`,
      `*Disclaimer: Indicative estimate only; final cost subject to official written cost sheet, PLC, statutory taxes & bank sanction.`,
      `Inspect in 360°: ${shareUrl}`,
    ].join("\n");

    try {
      await navigator.clipboard.writeText(shareText);
      setCopiedShare(true);
      setTimeout(() => setCopiedShare(false), 2600);
    } catch {
      setCopiedShare(true);
      setTimeout(() => setCopiedShare(false), 2600);
    }
  };

  const resetOrbit = () => {
    const ctrl = controlsRef.current;
    if (!ctrl) return;
    ctrl.object.position.set(16, 13.5, 19);
    ctrl.target.set(0, 0.8, 0);
    ctrl.update();
  };

  const widthFt = (plot.w * 3.28084).toFixed(1);
  const depthFt = (plot.d * 3.28084).toFixed(1);
  const statusInfo = STATUS_META[plot.status];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="popout-plot-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-3 sm:p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative grid max-h-[94svh] w-full max-w-6xl grid-cols-1 overflow-hidden rounded-2xl border border-[#D4AF5F]/30 bg-[#0b120f] text-[#F2EFE7] lg:grid-cols-12">
        {/* Left: Full 360° Interactive Three.js Plot Pedestal */}
        <div className="relative min-h-[360px] bg-[#08100d] lg:col-span-7 lg:min-h-[640px]">
          <div ref={stageRef} className="h-full w-full" />

          {/* Top-Left Parcel Header (solid rgba, zero backdrop-filter over WebGL) */}
          <div className="pointer-events-none absolute top-4 left-4 right-16 flex flex-wrap items-center justify-between gap-2">
            <div className="rounded-lg border border-[#D4AF5F]/25 bg-[#0a1410]/95 px-3.5 py-2">
              <p className="text-[11px] tracking-wider text-[#D4AF5F]">
                {project.name}
                {plot.wing ? ` · ${plot.wing}` : ""} · 360° Land Inspector
              </p>
              <div className="flex items-center gap-2.5">
                <h3 id="popout-plot-title" className="text-2xl font-normal text-[#F7F4EC]">
                  Plot {plot.displayNo ?? plot.no}
                </h3>
                {plot.status === "sold" ? (
                  <span className="rounded border border-[#C9735B]/60 bg-[#76261C]/85 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-[#F7E8D0] uppercase">
                    Sold Out · Allotted
                  </span>
                ) : null}
              </div>
            </div>

            {/* Live Synchronized Rotational Compass Dial */}
            <div className="flex items-center gap-3 rounded-lg border border-[#D4AF5F]/30 bg-[#0a1410]/95 px-3.5 py-2">
              <div className="relative flex h-11 w-11 items-center justify-center rounded-full border border-[#D4AF5F]/40 bg-[#070d0a]">
                <span className="absolute top-0.5 text-[8px] font-bold text-[#E5C77B]">N</span>
                <span className="absolute bottom-0.5 text-[8px] text-[#7c8c81]">S</span>
                <span className="absolute right-1 text-[8px] text-[#7c8c81]">E</span>
                <span className="absolute left-1 text-[8px] text-[#7c8c81]">W</span>
                <div
                  style={{ transform: `rotate(${-cameraAzimuth}deg)` }}
                  className="h-6 w-0.5 origin-center transition-transform duration-75"
                >
                  <div className="h-3 w-full rounded-t bg-[#E5C77B]" />
                  <div className="h-3 w-full rounded-b bg-[#48594f]" />
                </div>
              </div>
              <div className="text-xs">
                <p className="font-mono-tabular text-[#E5C77B]">
                  {String(cameraAzimuth).padStart(3, "0")}° {azimuthToCardinal(cameraAzimuth)}
                </p>
                <p className="text-[11px] text-[#B6C3BA]">
                  Fronts {FACING_LABEL[plot.faces]} ({FACING_AZIMUTH[plot.faces]}°)
                </p>
              </div>
            </div>
          </div>

          {/* Compact Bottom Control Bar over 360° Canvas */}
          <div className="absolute inset-x-4 bottom-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/10 bg-[#0a1410]/95 px-3.5 py-2 text-xs">
            <span className="text-[#AEBBAE]">Drag to orbit 360° · Scroll to zoom</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setAutoRotate((a) => !a)}
                className="inline-flex items-center gap-1.5 rounded-md border border-white/15 bg-[#12201a] px-2.5 py-1 text-xs font-medium text-[#E9E6DA] hover:border-[#D4AF5F]/50"
              >
                {autoRotate ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                <span>{autoRotate ? "Pause Spin" : "360° Spin"}</span>
              </button>
              <button
                type="button"
                onClick={resetOrbit}
                className="inline-flex items-center gap-1 rounded-md border border-white/15 bg-[#12201a] px-2.5 py-1 text-xs font-medium text-[#E9E6DA] hover:border-[#D4AF5F]/50"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span>Reset</span>
              </button>
            </div>
          </div>
        </div>

        {/* Right: Plot Specifications, Share & Live Budget + Loan Estimator */}
        <div className="flex max-h-[58svh] flex-col overflow-y-auto border-t border-white/10 bg-[#101814] p-5 sm:p-6 lg:col-span-5 lg:max-h-[94svh] lg:border-t-0 lg:border-l">
          <div className="flex items-start justify-between gap-4 border-b border-white/10 pb-4">
            <div>
              <div className="flex items-center gap-2 text-xs text-[#C7D1C9]">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-xs"
                  style={{ backgroundColor: statusInfo.color }}
                />
                <span className="font-medium text-white">{statusInfo.label}</span>
                <span aria-hidden="true">·</span>
                <span>{statusInfo.badgeText}</span>
              </div>
              <p className="mt-1.5 text-xs text-[#9FB0A4]">{plot.vastuNote}</p>
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close 360 plot inspector"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/15 bg-[#16231d] text-[#E6E3D7] transition-colors hover:border-[#D4AF5F] hover:text-[#D4AF5F]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Measurements Grid */}
          <div className="mt-4 grid grid-cols-2 gap-3 border-b border-white/10 pb-4 text-xs">
            <div className="rounded-lg border border-white/10 bg-[#0b120f] p-3">
              <div className="flex items-center gap-1.5 text-[#98A99D]">
                <Maximize2 className="h-3.5 w-3.5 text-[#D4AF5F]" />
                <span>Dimensions</span>
              </div>
              <p className="font-mono-tabular mt-1 text-sm font-medium text-white">
                {plot.w}m × {plot.d}m
              </p>
              <p className="font-mono-tabular text-[11px] text-[#98A99D]">
                {widthFt} ft × {depthFt} ft
              </p>
            </div>
            <div className="rounded-lg border border-white/10 bg-[#0b120f] p-3">
              <div className="flex items-center gap-1.5 text-[#98A99D]">
                <Compass className="h-3.5 w-3.5 text-[#D4AF5F]" />
                <span>Plot Area</span>
              </div>
              <p className="font-mono-tabular mt-1 text-sm font-medium text-white">
                {plot.areaSqFt.toLocaleString("en-IN")} sq.ft
              </p>
              <p className="font-mono-tabular text-[11px] text-[#98A99D]">
                {plot.areaSqM} sq.m · {plot.category ?? "Residential"} · {plot.corner ? "Corner" : "Standard"}
                {plot.parkFacing ? " / Park" : ""}
              </p>
            </div>
            <div className="col-span-2 flex items-center justify-between rounded-lg border border-white/10 bg-[#0b120f] px-3 py-2">
              <span className="text-[#98A99D]">Frontage Road &amp; Orientation</span>
              <span className="font-mono-tabular font-medium text-[#E5C77B]">
                {plot.road} · {FACING_LABEL[plot.faces]}
              </span>
            </div>
          </div>

          {/* Instant Budget & Home Loan EMI Calculator */}
          <div className="mt-4 space-y-3.5">
            <div className="flex items-baseline justify-between">
              <h4 className="font-display text-xl text-[#F5F2E9]">Budget &amp; Loan Estimate</h4>
              <span className="font-mono-tabular text-xs text-[#D4AF5F]">
                PLC {plot.plcPercent}%
              </span>
            </div>

            <div className="space-y-3 rounded-xl border border-white/10 bg-[#0b120f] p-4">
              <div>
                <div className="flex justify-between text-xs">
                  <span className="text-[#A8B7AC]">Indicative Rate / sq.ft</span>
                  <span className="font-mono-tabular font-medium text-white">
                    {formatINR(ratePerSqFt)}
                  </span>
                </div>
                <input
                  type="range"
                  aria-label="Indicative Rate per square foot"
                  min={1400}
                  max={4000}
                  step={10}
                  value={ratePerSqFt}
                  onChange={(e) => {
                    setRatePerSqFt(Number(e.target.value));
                    trackEvent({
                      type: "calculator_update",
                      page: `/projects/${project.slug}`,
                      project: project.slug,
                      plotNumber: plot.no,
                    });
                  }}
                  className="estate-slider mt-1.5 w-full"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="flex justify-between text-xs">
                    <span className="text-[#A8B7AC]">Down Payment</span>
                    <span className="font-mono-tabular text-white">{downPaymentPct}%</span>
                  </div>
                  <input
                    type="range"
                    aria-label="Down Payment Percentage"
                    min={15}
                    max={80}
                    step={5}
                    value={downPaymentPct}
                    onChange={(e) => setDownPaymentPct(Number(e.target.value))}
                    className="estate-slider mt-1.5 w-full"
                  />
                </div>
                <div>
                  <div className="flex justify-between text-xs">
                    <span className="text-[#A8B7AC]">Loan Tenure</span>
                    <span className="font-mono-tabular text-white">{tenureYears} yrs</span>
                  </div>
                  <input
                    type="range"
                    aria-label="Loan Tenure in Years"
                    min={5}
                    max={25}
                    step={1}
                    value={tenureYears}
                    onChange={(e) => setTenureYears(Number(e.target.value))}
                    className="estate-slider mt-1.5 w-full"
                  />
                </div>
              </div>

              <div className="flex items-center justify-between gap-2 pt-1 text-xs">
                <label className="flex cursor-pointer items-center gap-2 text-[#B8C4BB]">
                  <input
                    type="checkbox"
                    checked={womenConcession}
                    onChange={(e) => setWomenConcession(e.target.checked)}
                    className="h-3.5 w-3.5 accent-[#D4AF5F]"
                  />
                  <span>Maharashtra Women Buyer (5% Stamp Duty)</span>
                </label>
                <div className="flex items-center gap-1.5">
                  <label htmlFor="plot-roi-input" className="text-[#8E9F93]">
                    ROI
                  </label>
                  <input
                    id="plot-roi-input"
                    type="number"
                    min={7}
                    max={14}
                    step={0.1}
                    value={interestRate}
                    onChange={(e) => setInterestRate(Number(e.target.value) || 8.85)}
                    className="font-mono-tabular w-16 rounded border border-white/15 bg-[#121c17] px-1.5 py-0.5 text-right text-xs text-white"
                  />
                  <span className="text-[#8E9F93]">%</span>
                </div>
              </div>
            </div>

            {/* Cost & EMI Summary Output */}
            <div className="grid grid-cols-2 gap-3 rounded-xl border border-[#D4AF5F]/30 bg-[#14201a] p-4">
              <div>
                <p className="text-[11px] text-[#AEBBAE]">All-Inclusive Estimate</p>
                <p className="font-mono-tabular mt-0.5 text-lg font-semibold text-[#F5F2E9]">
                  {formatINRCompact(finance.totalAcquisitionCost)}
                </p>
                <p className="font-mono-tabular mt-0.5 text-[11px] text-[#96A79A]">
                  Stamp + Reg: {formatINRCompact(finance.stampDuty + finance.registrationFee)}
                </p>
              </div>
              <div className="border-l border-white/10 pl-3">
                <p className="text-[11px] text-[#D4AF5F]">Est. Monthly Loan EMI</p>
                <p className="font-mono-tabular mt-0.5 text-lg font-semibold text-[#E5C77B]">
                  {formatINR(finance.monthlyEmi)}
                </p>
                <p className="font-mono-tabular mt-0.5 text-[11px] text-[#96A79A]">
                  Loan: {formatINRCompact(finance.loanPrincipal)}
                </p>
              </div>
            </div>
            {/* Eligible Nagpur Plot Loan Banks */}
            <LoanPartnerBanks variant="compact" />

            {/* Mandatory Business & Customer Protection Pricing Disclaimer */}
            <PricingDisclaimer variant="compact" surface="dark" />
          </div>

          {/* Primary Actions */}
          <div className="mt-5 flex flex-col gap-2.5 pt-2">
            <button
              type="button"
              onClick={() => onReservePlot(plot, finance.totalAcquisitionCost, true)}
              className="min-h-[46px] w-full rounded-lg bg-[#D4AF5F] px-4 py-3 text-sm font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
            >
              {plot.status === "sold"
                ? `Inquire Similar Available Plots to Plot ${plot.displayNo ?? plot.no}`
                : `Request Written Cost Sheet for Plot ${plot.displayNo ?? plot.no}`}
            </button>

            <button
              type="button"
              onClick={handleShare}
              className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-lg border border-white/15 bg-[#15221c] px-4 py-2.5 text-xs font-medium text-[#EFECE2] transition-colors hover:border-[#D4AF5F]/60"
            >
              {copiedShare ? (
                <>
                  <Check className="h-3.5 w-3.5 text-[#6FA07A]" />
                  <span>Plot Brief &amp; 360° Link Copied for WhatsApp</span>
                </>
              ) : (
                <>
                  <WhatsAppIcon className="h-3.5 w-3.5 text-[#25D366]" />
                  <Share2 className="h-3.5 w-3.5 text-[#D4AF5F]" />
                  <span>Share Plot Availability &amp; Price Brief</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
