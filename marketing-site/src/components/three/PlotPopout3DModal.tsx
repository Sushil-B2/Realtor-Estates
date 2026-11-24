import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import {
  Check,
  Compass,
  Maximize2,
  Minus,
  Move,
  Pause,
  Play,
  Plus,
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

function extractRoadWidthMeters(roadLabel: string): string {
  const match = roadLabel.match(/(\d+(?:\.\d+)?)\s*m/i);
  if (match) {
    const meters = Number(match[1]);
    const feet = Math.round(meters * 3.28084);
    return `${meters.toFixed(2)}m (${feet} ft) ROAD WIDTH`;
  }
  return `${roadLabel.toUpperCase()} WIDTH`;
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
    const prevOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.documentElement.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
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

  // Mount dedicated 360-degree individual plot diorama with full pan/zoom reachability & unobstructed road markings
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
    scene.fog = new THREE.FogExp2(0x08100d, 0.0075);

    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    pmrem.dispose();

    // Wider framing & unobstructed zoom/pan controls so outer corners, road markings, and compass are always reachable
    const camera = new THREE.PerspectiveCamera(42, 1, 0.2, 320);
    camera.position.set(19.5, 16.8, 23.5);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = !isSoftwareGl;
    controls.dampingFactor = 0.065;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.panSpeed = 0.95;
    controls.zoomSpeed = 0.95;
    controls.minDistance = 7.5;
    controls.maxDistance = 58;
    controls.minPolarAngle = 0.16;
    controls.maxPolarAngle = Math.PI / 2 - 0.06;
    controls.autoRotate = !isSoftwareGl;
    controls.autoRotateSpeed = 0.72;
    controls.target.set(0, 0.9, 0);
    controlsRef.current = controls;

    // 3-Point Studio Lighting Rig
    scene.add(new THREE.HemisphereLight(0xd9eadf, 0x16241d, 1.48));
    const keyLight = new THREE.DirectionalLight(0xffe0aa, 2.75);
    keyLight.position.set(-16, 26, 18);
    if (!isSoftwareGl) {
      keyLight.castShadow = true;
      keyLight.shadow.mapSize.set(1024, 1024);
      keyLight.shadow.camera.left = -24;
      keyLight.shadow.camera.right = 24;
      keyLight.shadow.camera.top = 24;
      keyLight.shadow.camera.bottom = -24;
      keyLight.shadow.bias = -0.0004;
      keyLight.shadow.normalBias = 0.02;
    }
    scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x88bfa4, 1.0);
    rimLight.position.set(18, 12, -20);
    scene.add(rimLight);

    // Normalize plot scale to fit hero pedestal nicely while preserving true aspect ratio
    const maxSide = Math.max(plot.w, plot.d);
    const unitScale = 11.8 / maxSide;
    const pw = plot.w * unitScale;
    const pd = plot.d * unitScale;
    const panLimit = Math.max(pw, pd) * 0.95 + 6.5;

    // Soft contact shadow plane under the floating architectural earth plinth
    const shadowTex = createContactShadowTexture();
    const shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(pw + 14, pd + 14),
      new THREE.MeshBasicMaterial({
        map: shadowTex,
        transparent: true,
        opacity: 0.52,
        depthWrite: false,
      }),
    );
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.position.y = -0.45;
    scene.add(shadowPlane);

    // 1. Subterranean stone & earth plinth
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

    // 3B. If parcel is SOLD OUT, mount a 3D residence & "SOLD OUT · ALLOTTED" plaque
    if (plot.status === "sold") {
      const soldVilla = buildSoldPropertyModel(renderer, pw, pd);
      soldVilla.position.set(0, 0.95, 0);
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
    // Generous gap (roadGap = 3.1) guarantees clean visual separation between plot edge and road markings
    const asphaltTex = createAsphaltTexture(renderer);
    const roadMat = new THREE.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.85,
      metalness: 0.06,
    });
    const isHorizontalRoad = plot.faces === "N" || plot.faces === "S";
    const roadThickness = 3.5;
    const roadLength = isHorizontalRoad ? pw + 6.4 : pd + 6.4;
    const roadMesh = new THREE.Mesh(
      new THREE.BoxGeometry(
        isHorizontalRoad ? roadLength : roadThickness,
        0.34,
        isHorizontalRoad ? roadThickness : roadLength,
      ),
      roadMat,
    );
    const roadGap = 3.15;
    if (plot.faces === "N") roadMesh.position.set(0, 0.56, -(pd / 2 + roadGap));
    else if (plot.faces === "S") roadMesh.position.set(0, 0.56, pd / 2 + roadGap);
    else if (plot.faces === "E") roadMesh.position.set(pw / 2 + roadGap, 0.56, 0);
    else roadMesh.position.set(-(pw / 2 + roadGap), 0.56, 0);
    roadMesh.receiveShadow = true;
    scene.add(roadMesh);

    // Helper to draw crisp architectural dimension line with perpendicular end-ticks (always rendered clearly)
    const addDimensionLine = (
      start: THREE.Vector3,
      end: THREE.Vector3,
      tickAxis: THREE.Vector3,
      colorHex: number,
    ) => {
      const mat = new THREE.LineBasicMaterial({
        color: colorHex,
        depthTest: false,
        transparent: true,
        opacity: 0.95,
      });
      const halfTick = tickAxis.clone().multiplyScalar(0.38);
      const segments = [
        start,
        end,
        start.clone().sub(halfTick),
        start.clone().add(halfTick),
        end.clone().sub(halfTick),
        end.clone().add(halfTick),
      ];
      const line = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segments), mat);
      line.renderOrder = 20;
      scene.add(line);
    };

    // 5B. Dedicated Between-Plot & Fronting Road Width Measurement Callout (100% Unobstructed, alwaysOnTop)
    const roadDimY = 1.08;
    const halfRoad = roadThickness / 2;
    const roadWidthCalloutText = extractRoadWidthMeters(plot.road);
    if (isHorizontalRoad) {
      const roadDimX = pw / 2 + 1.85;
      const z1 = roadMesh.position.z - halfRoad;
      const z2 = roadMesh.position.z + halfRoad;
      addDimensionLine(
        new THREE.Vector3(roadDimX, roadDimY, z1),
        new THREE.Vector3(roadDimX, roadDimY, z2),
        new THREE.Vector3(1, 0, 0),
        0xf5d47a,
      );
      const roadWidthSprite = createLabelSprite(renderer, roadWidthCalloutText, 6.2, 1.24, {
        bg: "rgba(8, 18, 13, 0.98)",
        borderColor: "#F5D47A",
        textColor: "#FFF5D6",
        accent: true,
        alwaysOnTop: true,
        renderOrder: 26,
        fontSize: 40,
      });
      roadWidthSprite.position.set(roadDimX + 1.4, 2.05, roadMesh.position.z);
      scene.add(roadWidthSprite);
    } else {
      const roadDimZ = pd / 2 + 1.85;
      const x1 = roadMesh.position.x - halfRoad;
      const x2 = roadMesh.position.x + halfRoad;
      addDimensionLine(
        new THREE.Vector3(x1, roadDimY, roadDimZ),
        new THREE.Vector3(x2, roadDimY, roadDimZ),
        new THREE.Vector3(0, 0, 1),
        0xf5d47a,
      );
      const roadWidthSprite = createLabelSprite(renderer, roadWidthCalloutText, 6.2, 1.24, {
        bg: "rgba(8, 18, 13, 0.98)",
        borderColor: "#F5D47A",
        textColor: "#FFF5D6",
        accent: true,
        alwaysOnTop: true,
        renderOrder: 26,
        fontSize: 40,
      });
      roadWidthSprite.position.set(roadMesh.position.x, 2.05, roadDimZ + 1.35);
      scene.add(roadWidthSprite);
    }

    // Road frontage banner elevated above the center of the fronting road
    const roadLabel = createLabelSprite(renderer, plot.road.toUpperCase(), 7.4, 1.38, {
      bg: "rgba(8, 18, 13, 0.98)",
      borderColor: "#F5D47A",
      textColor: "#FFF5D6",
      accent: true,
      alwaysOnTop: true,
      renderOrder: 25,
      fontSize: 44,
    });
    roadLabel.position.set(roadMesh.position.x, 3.05, roadMesh.position.z);
    scene.add(roadLabel);

    const lamp = buildStreetLamp(0.75);
    lamp.position.set(
      plot.faces === "E" ? pw / 2 + 1.05 : plot.faces === "W" ? -pw / 2 - 1.05 : -pw / 2 - 0.95,
      0.56,
      plot.faces === "N" ? -pd / 2 - 1.05 : plot.faces === "S" ? pd / 2 + 1.05 : -pd / 2 + 0.8,
    );
    scene.add(lamp);

    const accentTree = buildCompositeTree(0.82, 0.5);
    accentTree.position.set(-pw / 2 + 1.3, 0.95, -pd / 2 + 1.3);
    scene.add(accentTree);

    // 6. 3D Measurement Dimension Bars (Width & Depth in meters & feet — alwaysOnTop so zero edge is hidden)
    const widthFt = (plot.w * 3.28084).toFixed(1);
    const depthFt = (plot.d * 3.28084).toFixed(1);

    // Width bar (along X, placed cleanly on the opposite side of the fronting road)
    const widthZ = plot.faces === "S" ? -pd / 2 - 1.85 : pd / 2 + 1.85;
    addDimensionLine(
      new THREE.Vector3(-pw / 2, 1.12, widthZ),
      new THREE.Vector3(pw / 2, 1.12, widthZ),
      new THREE.Vector3(0, 0, 1),
      0xe5c77b,
    );

    const widthSprite = createLabelSprite(renderer, `WIDTH: ${plot.w}m (${widthFt} ft)`, 5.4, 1.24, {
      bg: "rgba(9, 18, 14, 0.98)",
      borderColor: "rgba(212, 175, 95, 0.85)",
      textColor: "#FAF8F2",
      alwaysOnTop: true,
      renderOrder: 24,
      fontSize: 38,
    });
    widthSprite.position.set(0, 2.18, widthZ);
    scene.add(widthSprite);

    // Depth bar (along Z, placed cleanly on the opposite side of the fronting road)
    const depthX = plot.faces === "E" ? -pw / 2 - 1.95 : pw / 2 + 1.95;
    addDimensionLine(
      new THREE.Vector3(depthX, 1.12, -pd / 2),
      new THREE.Vector3(depthX, 1.12, pd / 2),
      new THREE.Vector3(1, 0, 0),
      0xe5c77b,
    );

    const depthSprite = createLabelSprite(renderer, `DEPTH: ${plot.d}m (${depthFt} ft)`, 5.4, 1.24, {
      bg: "rgba(9, 18, 14, 0.98)",
      borderColor: "rgba(212, 175, 95, 0.85)",
      textColor: "#FAF8F2",
      alwaysOnTop: true,
      renderOrder: 24,
      fontSize: 38,
    });
    depthSprite.position.set(depthX, 2.18, 0);
    scene.add(depthSprite);

    // 7. Full 360° Engraved Brass Compass Ring around the Plot Pedestal
    const compassRadius = Math.max(pw, pd) * 0.72 + 3.4;
    const compassRing = buildCompassMonument(compassRadius);
    compassRing.position.set(0, -0.1, 0);
    scene.add(compassRing);

    const cardinals: Array<[string, number, number, boolean]> = [
      ["N", 0, -compassRadius - 1.25, true],
      ["S", 0, compassRadius + 1.25, false],
      ["E", compassRadius + 1.25, 0, false],
      ["W", -compassRadius - 1.25, 0, false],
    ];
    cardinals.forEach(([txt, x, z, isNorth]) => {
      const sp = createLabelSprite(renderer, txt, 2.3, 1.32, {
        accent: isNorth,
        alwaysOnTop: true,
        renderOrder: 22,
      });
      sp.position.set(x, 1.05, z);
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

      // Keep pan target cleanly inside plot + road + compass envelope so outer edges stay reachable without losing scene
      controls.target.x = THREE.MathUtils.clamp(controls.target.x, -panLimit, panLimit);
      controls.target.z = THREE.MathUtils.clamp(controls.target.z, -panLimit, panLimit);
      controls.target.y = THREE.MathUtils.clamp(controls.target.y, 0.2, 3.6);

      // Compute camera azimuth relative to True North (-Z)
      const relX = camera.position.x - controls.target.x;
      const relZ = camera.position.z - controls.target.z;
      const angleRad = Math.atan2(relX, relZ);
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
        if (
          obj instanceof THREE.Mesh ||
          obj instanceof THREE.Line ||
          obj instanceof THREE.LineSegments ||
          obj instanceof THREE.Sprite
        ) {
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
    ctrl.object.position.set(19.5, 16.8, 23.5);
    ctrl.target.set(0, 0.9, 0);
    ctrl.update();
  };

  const zoomCamera = (factor: number) => {
    const ctrl = controlsRef.current;
    if (!ctrl) return;
    const offset = ctrl.object.position.clone().sub(ctrl.target);
    const nextDist = THREE.MathUtils.clamp(offset.length() * factor, 7.5, 58);
    offset.setLength(nextDist);
    ctrl.object.position.copy(ctrl.target).add(offset);
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
      data-lenis-prevent
      data-lenis-prevent-wheel
      data-lenis-prevent-touch
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-2 sm:p-4 lg:p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        data-lenis-prevent
        data-lenis-prevent-wheel
        data-lenis-prevent-touch
        className="relative flex h-[94svh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[#D4AF5F]/30 bg-[#0b120f] text-[#F2EFE7] sm:rounded-3xl lg:grid lg:h-[min(90svh,820px)] lg:min-h-[540px] lg:grid-cols-12"
      >
        {/* Left / Top: Full 360° Interactive Three.js Plot Pedestal */}
        <div className="relative h-[38svh] min-h-[235px] max-h-[340px] w-full shrink-0 bg-[#08100d] sm:h-[42svh] sm:max-h-[380px] lg:col-span-7 lg:h-full lg:max-h-none lg:min-h-0">
          <div ref={stageRef} className="h-full w-full" />

          {/* Top-Left Parcel Header & Top-Right Compass + Mobile Close */}
          <div className="pointer-events-none absolute top-3 left-3 right-3 flex items-start justify-between gap-2 sm:top-4 sm:left-4 sm:right-4">
            <div className="min-w-0 rounded-xl border border-[#D4AF5F]/25 bg-[#0a1410]/95 px-2.5 py-1.5 sm:px-3.5 sm:py-2">
              <p className="truncate text-[10px] font-medium text-[#D4AF5F] sm:text-[11px]">
                {project.name}
                {plot.wing ? ` · ${plot.wing}` : ""}
              </p>
              <div className="flex items-center gap-2">
                <h3
                  id="popout-plot-title"
                  className="text-base font-semibold tracking-tight text-[#F7F4EC] sm:text-xl"
                >
                  Plot {plot.displayNo ?? plot.no}
                </h3>
                {plot.status === "sold" ? (
                  <span className="rounded-md border border-[#C9735B]/60 bg-[#76261C]/85 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-[#F7E8D0] uppercase sm:px-2 sm:text-[10px]">
                    Sold Out
                  </span>
                ) : null}
              </div>
            </div>

            <div className="pointer-events-auto flex items-start gap-2">
              {/* Live Synchronized Rotational Compass Dial */}
              <div className="flex items-center gap-2 rounded-xl border border-[#D4AF5F]/30 bg-[#0a1410]/95 px-2.5 py-1.5 sm:gap-2.5 sm:px-3">
                <div className="relative flex h-8 w-8 items-center justify-center rounded-full border border-[#D4AF5F]/40 bg-[#070d0a] sm:h-10 sm:w-10">
                  <span className="absolute top-0.5 text-[7px] font-bold text-[#E5C77B] sm:text-[8px]">
                    N
                  </span>
                  <span className="absolute bottom-0.5 text-[7px] text-[#7c8c81] sm:text-[8px]">
                    S
                  </span>
                  <span className="absolute right-1 text-[7px] text-[#7c8c81] sm:text-[8px]">E</span>
                  <span className="absolute left-1 text-[7px] text-[#7c8c81] sm:text-[8px]">W</span>
                  <div
                    style={{ transform: `rotate(${-cameraAzimuth}deg)` }}
                    className="h-4 w-0.5 origin-center transition-transform duration-75 sm:h-5"
                  >
                    <div className="h-2 w-full rounded-t bg-[#E5C77B] sm:h-2.5" />
                    <div className="h-2 w-full rounded-b bg-[#48594f] sm:h-2.5" />
                  </div>
                </div>
                <div className="hidden text-xs sm:block">
                  <p className="font-mono-tabular font-medium text-[#E5C77B]">
                    {String(cameraAzimuth).padStart(3, "0")}° {azimuthToCardinal(cameraAzimuth)}
                  </p>
                  <p className="text-[11px] text-[#B6C3BA]">
                    {FACING_LABEL[plot.faces]} ({FACING_AZIMUTH[plot.faces]}°)
                  </p>
                </div>
              </div>

              {/* Mobile / Tablet Quick Close Button */}
              <button
                type="button"
                onClick={onClose}
                aria-label="Close 360 plot inspector"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/20 bg-[#0a1410]/95 text-[#E6E3D7] transition-colors hover:border-[#D4AF5F] hover:text-[#D4AF5F] lg:hidden"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Compact Bottom Control Bar: Orbit + Pan + Zoom +/- + Reset */}
          <div className="absolute inset-x-3 bottom-3 flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-[#0a1410]/95 px-2.5 py-1.5 text-xs sm:inset-x-4 sm:bottom-4 sm:px-3.5 sm:py-2">
            <span className="hidden items-center gap-1.5 text-[11px] text-[#B6C4BA] sm:inline-flex">
              <Move className="h-3.5 w-3.5 text-[#D4AF5F]" aria-hidden="true" />
              <span>Drag to rotate · Right-drag or 2 fingers to pan</span>
            </span>
            <span className="inline-flex items-center gap-1 text-[10px] text-[#B6C4BA] sm:hidden">
              <Move className="h-3 w-3 text-[#D4AF5F]" aria-hidden="true" />
              <span>Drag to rotate</span>
            </span>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                aria-label="Zoom in"
                onClick={() => zoomCamera(0.8)}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-[#E9E6DA] hover:border-[#D4AF5F]"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="Zoom out"
                onClick={() => zoomCamera(1.24)}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 bg-[#12201a] text-[#E9E6DA] hover:border-[#D4AF5F]"
              >
                <Minus className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setAutoRotate((a) => !a)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/15 bg-[#12201a] px-2.5 text-xs font-medium text-[#E9E6DA] hover:border-[#D4AF5F]/60"
              >
                {autoRotate ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                <span>{autoRotate ? "Pause" : "Spin"}</span>
              </button>
              <button
                type="button"
                onClick={resetOrbit}
                className="inline-flex h-8 items-center gap-1 rounded-lg border border-white/15 bg-[#12201a] px-2.5 text-xs font-medium text-[#E9E6DA] hover:border-[#D4AF5F]/60"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Reset</span>
              </button>
            </div>
          </div>
        </div>

        {/* Right / Bottom: Plot Specifications & Live Budget + Loan Estimator */}
        <div className="flex min-h-0 flex-1 flex-col border-t border-white/10 bg-[#101814] lg:col-span-5 lg:h-full lg:border-t-0 lg:border-l">
          {/* Sticky Compact Status Header */}
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-white/10 bg-[#101814] px-4 py-3.5 sm:px-6 sm:py-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-xs text-[#C7D1C9]">
                <span
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-xs"
                  style={{ backgroundColor: statusInfo.color }}
                />
                <span className="font-semibold text-white">{statusInfo.label}</span>
                <span aria-hidden="true">·</span>
                <span>{statusInfo.badgeText}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[#9FB0A4]">
                {plot.vastuNote}
              </p>
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close 360 plot inspector"
              className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/15 bg-[#16231d] text-[#E6E3D7] transition-colors hover:border-[#D4AF5F] hover:text-[#D4AF5F] lg:flex"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Independently Scrollable Body (Trackpad / Wheel / Touch Ready) */}
          <div
            data-lenis-prevent
            data-lenis-prevent-wheel
            data-lenis-prevent-touch
            className="modal-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6 sm:py-5"
          >
            {/* Measurements Grid */}
            <div className="grid grid-cols-2 gap-2.5 border-b border-white/10 pb-4 text-xs sm:gap-3">
              <div className="rounded-xl border border-white/10 bg-[#0b120f] p-3">
                <div className="flex items-center gap-1.5 text-[#98A99D]">
                  <Maximize2 className="h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" />
                  <span>Dimensions</span>
                </div>
                <p className="font-mono-tabular mt-1 text-sm font-semibold text-white">
                  {plot.w}m × {plot.d}m
                </p>
                <p className="font-mono-tabular text-[11px] text-[#98A99D]">
                  {widthFt} ft × {depthFt} ft
                </p>
              </div>
              <div className="rounded-xl border border-white/10 bg-[#0b120f] p-3">
                <div className="flex items-center gap-1.5 text-[#98A99D]">
                  <Compass className="h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" />
                  <span>Plot Area</span>
                </div>
                <p className="font-mono-tabular mt-1 text-sm font-semibold text-white">
                  {plot.areaSqFt.toLocaleString("en-IN")} sq.ft
                </p>
                <p className="font-mono-tabular text-[11px] text-[#98A99D]">
                  {plot.areaSqM} sq.m · {plot.category ?? "Residential"}
                </p>
              </div>
              <div className="col-span-2 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-[#0b120f] px-3.5 py-2.5">
                <span className="text-[#98A99D]">Front Road &amp; Direction</span>
                <span className="font-mono-tabular font-semibold text-[#E5C77B]">
                  {plot.road} · {FACING_LABEL[plot.faces]}
                </span>
              </div>
            </div>

            {/* Instant Budget & Home Loan EMI Calculator */}
            <div className="mt-4 space-y-3.5">
              <div className="flex items-baseline justify-between">
                <h4 className="text-base font-semibold tracking-tight text-[#F5F2E9]">
                  Budget &amp; EMI Estimate
                </h4>
                <span className="font-mono-tabular text-xs text-[#D4AF5F]">
                  PLC {plot.plcPercent}%
                </span>
              </div>

              <div className="space-y-3 rounded-2xl border border-white/10 bg-[#0b120f] p-3.5 sm:p-4">
                <div>
                  <div className="flex justify-between text-xs">
                    <span className="text-[#A8B7AC]">Indicative Rate / sq.ft</span>
                    <span className="font-mono-tabular font-semibold text-white">
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
                    className="estate-slider mt-2 w-full"
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
                      className="estate-slider mt-2 w-full"
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
                      className="estate-slider mt-2 w-full"
                    />
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs">
                  <label className="flex cursor-pointer items-center gap-2 text-[#B8C4BB]">
                    <input
                      type="checkbox"
                      checked={womenConcession}
                      onChange={(e) => setWomenConcession(e.target.checked)}
                      className="h-3.5 w-3.5 accent-[#D4AF5F]"
                    />
                    <span>Women Buyer (5% Stamp Duty)</span>
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
                      className="font-mono-tabular w-16 rounded-md border border-white/15 bg-[#121c17] px-1.5 py-0.5 text-right text-xs text-white"
                    />
                    <span className="text-[#8E9F93]">%</span>
                  </div>
                </div>
              </div>

              {/* Cost & EMI Summary Output */}
              <div className="grid grid-cols-2 gap-3 rounded-2xl border border-[#D4AF5F]/30 bg-[#14201a] p-3.5 sm:p-4">
                <div>
                  <p className="text-[11px] text-[#AEBBAE]">All-Inclusive Estimate*</p>
                  <p className="font-mono-tabular mt-0.5 text-base font-semibold text-[#F5F2E9] sm:text-lg">
                    {formatINRCompact(finance.totalAcquisitionCost)}
                  </p>
                  <p className="font-mono-tabular mt-0.5 text-[11px] text-[#96A79A]">
                    Stamp + Reg: {formatINRCompact(finance.stampDuty + finance.registrationFee)}
                  </p>
                </div>
                <div className="border-l border-white/10 pl-3">
                  <p className="text-[11px] text-[#D4AF5F]">Est. Monthly EMI*</p>
                  <p className="font-mono-tabular mt-0.5 text-base font-semibold text-[#E5C77B] sm:text-lg">
                    {formatINR(finance.monthlyEmi)}
                  </p>
                  <p className="font-mono-tabular mt-0.5 text-[11px] text-[#96A79A]">
                    Loan: {formatINRCompact(finance.loanPrincipal)}
                  </p>
                </div>
              </div>

              <LoanPartnerBanks variant="compact" />
              <PricingDisclaimer variant="compact" surface="dark" />
            </div>

            {/* Primary Actions */}
            <div className="mt-5 flex flex-col gap-2.5 pb-2">
              <button
                type="button"
                onClick={() => onReservePlot(plot, finance.totalAcquisitionCost, true)}
                className="min-h-[46px] w-full rounded-xl bg-[#D4AF5F] px-4 py-3 text-sm font-semibold text-[#080C0A] transition-colors hover:bg-[#e5c77b]"
              >
                {plot.status === "sold"
                  ? `See Similar Open Plots to Plot ${plot.displayNo ?? plot.no}`
                  : `Get Written Cost Sheet for Plot ${plot.displayNo ?? plot.no}`}
              </button>

              <button
                type="button"
                onClick={handleShare}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-white/15 bg-[#15221c] px-4 py-2.5 text-xs font-medium text-[#EFECE2] transition-colors hover:border-[#D4AF5F]/60"
              >
                {copiedShare ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-[#6FA07A]" />
                    <span>Plot Details &amp; 360° Link Copied</span>
                  </>
                ) : (
                  <>
                    <WhatsAppIcon className="h-3.5 w-3.5 text-[#25D366]" />
                    <Share2 className="h-3.5 w-3.5 text-[#D4AF5F]" />
                    <span>Share Plot Details on WhatsApp</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
