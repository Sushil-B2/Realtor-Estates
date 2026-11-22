import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { ChevronDown, ChevronUp, Compass, Pause, Play, RotateCcw } from "lucide-react";
import {
  buildCompassMonument,
  buildCompositeTree,
  buildEstateGateway,
  buildStreetLamp,
  createAsphaltTexture,
  createContactShadowTexture,
  createGroundTexture,
  detectSoftwareWebGL,
  scheduleAfterDraw,
} from "./proceduralAssets";

const CHAPTERS = [
  {
    index: "01",
    kicker: "Arrival & Approach",
    title: "Begin at the grand boulevard.",
    copy: "Walk past the basalt stone gateway onto 12m to 30m concrete avenues. True estate value begins with road frontage, storm-water drainage, and serene approach symmetry.",
  },
  {
    index: "02",
    kicker: "Spatial Layout",
    title: "Every parcel in living context.",
    copy: "See how morning sunlight crosses East and North plots, how open botanical parks breathe between wings, and how every corner setback sits inside the wider Nagpur corridor.",
  },
  {
    index: "03",
    kicker: "Generational Legacy",
    title: "Inspect in 360° before you decide.",
    copy: "Open any of our four Nagpur layouts below, pop out any individual plot for a 360-degree measurement & compass tour, and calculate your exact budget and loan EMI.",
  },
];

// Module-scope reusable scratch objects (zero per-frame GC)
const _camTargetPos = new THREE.Vector3();
const _aimTargetPos = new THREE.Vector3();
const _currentAim = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _mat4 = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _scl = new THREE.Vector3();

export default function CinematicJourney3D({ onExploreEstates }: { onExploreEstates: () => void }) {
  const sectionRef = useRef<HTMLElement | null>(null);
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const [activeChapter, setActiveChapter] = useState(0);
  const [dockCollapsed, setDockCollapsed] = useState(false);
  const [autoFly, setAutoFly] = useState(false);
  const [webglUnavailable, setWebglUnavailable] = useState(false);

  const autoFlyRef = useRef(false);
  autoFlyRef.current = autoFly;
  const manualProgressRef = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key.toLowerCase() === "h" &&
        !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)
      ) {
        setDockCollapsed((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const section = sectionRef.current;
    const host = canvasHostRef.current;
    if (!section || !host) return;

    const isSoftwareGl = detectSoftwareWebGL();
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: !isSoftwareGl,
        powerPreference: "high-performance",
      });
    } catch {
      setWebglUnavailable(true);
      return;
    }

    renderer.setPixelRatio(isSoftwareGl ? 1 : Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    renderer.shadowMap.enabled = !isSoftwareGl;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x09130f, 1);

    const canvas = renderer.domElement;
    canvas.className = "block h-full w-full";
    canvas.setAttribute("aria-hidden", "true");
    host.appendChild(canvas);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x09130f);
    scene.fog = new THREE.FogExp2(0x09130f, 0.0115);

    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    pmrem.dispose();

    const camera = new THREE.PerspectiveCamera(40, 1, 0.2, 260);

    // Studio warm key + cool sky/ground fill + rim backlight
    const hemi = new THREE.HemisphereLight(0xcbe0d2, 0x16241d, 1.35);
    scene.add(hemi);

    const keySun = new THREE.DirectionalLight(0xffdca3, 2.75);
    keySun.position.set(-22, 34, 20);
    if (!isSoftwareGl) {
      keySun.castShadow = true;
      keySun.shadow.mapSize.set(1024, 1024);
      keySun.shadow.camera.left = -36;
      keySun.shadow.camera.right = 36;
      keySun.shadow.camera.top = 36;
      keySun.shadow.camera.bottom = -36;
      keySun.shadow.camera.near = 2;
      keySun.shadow.camera.far = 90;
      keySun.shadow.bias = -0.0005;
      keySun.shadow.normalBias = 0.02;
    }
    scene.add(keySun);

    const rimLight = new THREE.DirectionalLight(0x88bfa4, 0.95);
    rimLight.position.set(28, 16, -24);
    scene.add(rimLight);

    // Extended natural landscape plane (never boxed in floating walls)
    const groundTex = createGroundTexture(renderer, "#13261d", "#244333", 20);
    const terrain = new THREE.Mesh(
      new THREE.PlaneGeometry(240, 240),
      new THREE.MeshStandardMaterial({ map: groundTex, roughness: 0.94, metalness: 0.02 }),
    );
    terrain.rotation.x = -Math.PI / 2;
    terrain.position.y = 0;
    terrain.receiveShadow = true;
    scene.add(terrain);

    // Asphalt avenue network with vertical offset >= 0.04 to prevent Z-fighting
    const asphaltTex = createAsphaltTexture(renderer);
    const roadMat = new THREE.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.85,
      metalness: 0.06,
    });
    const addRoad = (w: number, d: number, x: number, z: number) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.14, d), roadMat);
      m.position.set(x, 0.07, z);
      m.receiveShadow = true;
      scene.add(m);
    };
    addRoad(46, 3.2, 0, -12);
    addRoad(46, 2.6, 0, 2);
    addRoad(46, 2.6, 0, 14);
    addRoad(3.2, 38, 0, 2);

    // Grand Estate Entrance Gateway
    const gateway = buildEstateGateway(0.56);
    gateway.position.set(0, 0.02, 20.5);
    scene.add(gateway);

    // Central Botanical Park & Brass Compass Monument
    const parkTurf = new THREE.Mesh(
      new THREE.CylinderGeometry(4.4, 4.6, 0.18, 40),
      new THREE.MeshStandardMaterial({ color: 0x2a553a, roughness: 0.88, metalness: 0.02 }),
    );
    parkTurf.position.set(0, 0.09, 2);
    parkTurf.receiveShadow = true;
    scene.add(parkTurf);

    const compassMonument = buildCompassMonument(3.1);
    compassMonument.position.set(0, 0.18, 2);
    scene.add(compassMonument);

    // Contact shadow plane beneath compass
    const shadowTex = createContactShadowTexture();
    const parkShadow = new THREE.Mesh(
      new THREE.PlaneGeometry(10.5, 10.5),
      new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, opacity: 0.42, depthWrite: false }),
    );
    parkShadow.rotation.x = -Math.PI / 2;
    parkShadow.position.set(0, 0.03, 2);
    scene.add(parkShadow);

    // Instanced sculpted residential parcels (36 plots with subtle height & status color)
    const parcelGeo = new THREE.BoxGeometry(1, 1, 1);
    const parcelMat = new THREE.MeshStandardMaterial({ roughness: 0.68, metalness: 0.1 });
    const cols = [-17, -12.5, -8, 8, 12.5, 17];
    const rows = [-16.5, -7.2, -2.8, 6.8, 10.8, 18.2];
    const totalPlots = cols.length * rows.length;
    const plotsInstanced = new THREE.InstancedMesh(parcelGeo, parcelMat, totalPlots);
    plotsInstanced.castShadow = !isSoftwareGl;
    plotsInstanced.receiveShadow = true;

    const availColor = new THREE.Color(0x6fa07a);
    const resColor = new THREE.Color(0xd4af5f);
    const soldColor = new THREE.Color(0x9b5f45);

    let idx = 0;
    rows.forEach((z, rIdx) => {
      cols.forEach((x, cIdx) => {
        const h = (rIdx + cIdx) % 3 === 0 ? 0.56 : 0.44;
        _pos.set(x, h / 2 + 0.04, z);
        _scl.set(3.7, h, 3.2);
        _quat.identity();
        _mat4.compose(_pos, _quat, _scl);
        plotsInstanced.setMatrixAt(idx, _mat4);

        const mod = (idx * 7 + 3) % 11;
        const c = mod < 5 ? availColor : mod < 8 ? soldColor : resColor;
        plotsInstanced.setColorAt(idx, c);
        idx++;
      });
    });
    plotsInstanced.instanceMatrix.needsUpdate = true;
    if (plotsInstanced.instanceColor) plotsInstanced.instanceColor.needsUpdate = true;
    plotsInstanced.computeBoundingSphere();
    scene.add(plotsInstanced);

    // Composite streetlamps along central spine
    [-10, -4, 8, 15].forEach((z) => {
      const lampL = buildStreetLamp(0.55);
      lampL.position.set(-2.1, 0, z);
      scene.add(lampL);

      const lampR = buildStreetLamp(0.55);
      lampR.position.set(2.1, 0, z);
      lampR.rotation.y = Math.PI;
      scene.add(lampR);
    });

    // Composite botanical trees framing the outer perimeter & park
    const treeCoords: Array<[number, number, number]> = [
      [-22, -16, 1.05], [-23, -6, 0.92], [-22, 5, 1.12], [-23, 16, 0.95],
      [22, -16, 1.0], [23, -5, 1.08], [22, 6, 0.88], [23, 17, 1.04],
      [-5.5, -14.5, 0.82], [5.5, -14.5, 0.86], [-5.2, 16.5, 0.85], [5.2, 16.5, 0.9],
      [-14, -22, 1.15], [0, -22, 1.2], [14, -22, 1.1],
    ];
    treeCoords.forEach(([tx, tz, sc], tIdx) => {
      const tree = buildCompositeTree(sc, (tIdx % 3) - 1);
      tree.position.set(tx, 0, tz);
      tree.rotation.y = tIdx * 1.1;
      scene.add(tree);
    });

    // Golden spline flight path guide along the avenue
    const routeCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0.22, 22),
      new THREE.Vector3(0, 0.22, 10),
      new THREE.Vector3(-4.8, 0.22, 2),
      new THREE.Vector3(0, 0.22, -6),
      new THREE.Vector3(10, 0.22, -12),
    ]);
    const routeTube = new THREE.Mesh(
      new THREE.TubeGeometry(routeCurve, 72, 0.08, 6, false),
      new THREE.MeshStandardMaterial({
        color: 0xe5c77b,
        emissive: 0x8b6722,
        emissiveIntensity: 0.65,
        roughness: 0.3,
        metalness: 0.8,
      }),
    );
    scene.add(routeTube);

    // Cinematic Camera Rig Spline
    const cameraSpline = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-22, 16, 32),
      new THREE.Vector3(-12, 11.5, 22),
      new THREE.Vector3(0, 9.5, 15),
      new THREE.Vector3(14, 11, 8),
      new THREE.Vector3(24, 16.5, -14),
    ]);
    const aimSpline = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 1.2, 14),
      new THREE.Vector3(0, 0.8, 5),
      new THREE.Vector3(0, 0.5, 1),
      new THREE.Vector3(2, 0.4, -4),
      new THREE.Vector3(0, 0.4, -2),
    ]);

    if (!isSoftwareGl) {
      renderer.compile(scene, camera);
    }

    let scrollProgress = 0;
    let smoothedProgress = 0;
    let lastChapter = -1;
    let frameId = 0;
    let disposed = false;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const clock = new THREE.Clock();

    const readScroll = () => {
      const rect = section.getBoundingClientRect();
      const travel = Math.max(section.offsetHeight - window.innerHeight, 1);
      scrollProgress = THREE.MathUtils.clamp(-rect.top / travel, 0, 1);
    };

    const resize = () => {
      const w = Math.max(host.clientWidth, 1);
      const h = Math.max(host.clientHeight, 1);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };

    const onContextLost = (ev: Event) => {
      ev.preventDefault();
      setWebglUnavailable(true);
    };
    canvas.addEventListener("webglcontextlost", onContextLost);

    readScroll();
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);
    window.addEventListener("scroll", readScroll, { passive: true });

    _currentAim.copy(aimSpline.getPoint(0));

    const tick = () => {
      if (disposed) return;
      const dt = Math.min(clock.getDelta(), 0.1);

      let targetP = scrollProgress;
      if (manualProgressRef.current !== null) {
        targetP = manualProgressRef.current;
        if (Math.abs(smoothedProgress - targetP) < 0.008) {
          manualProgressRef.current = null;
        }
      } else if (autoFlyRef.current && !reducedMotion) {
        targetP = (smoothedProgress + dt * 0.085) % 1;
        scrollProgress = targetP;
      }

      smoothedProgress = reducedMotion
        ? targetP
        : THREE.MathUtils.damp(smoothedProgress, targetP, 5.5, dt);

      cameraSpline.getPoint(smoothedProgress, _camTargetPos);
      aimSpline.getPoint(smoothedProgress, _aimTargetPos);
      camera.position.copy(_camTargetPos);
      _currentAim.lerp(_aimTargetPos, reducedMotion ? 1 : 1 - Math.exp(-6 * dt));
      camera.lookAt(_currentAim);

      // Subtle living compass pulse rotation
      compassMonument.rotation.y = Math.sin(clock.elapsedTime * 0.25) * 0.04;

      const nextChapter = Math.min(CHAPTERS.length - 1, Math.floor(smoothedProgress * CHAPTERS.length));
      if (nextChapter !== lastChapter) {
        lastChapter = nextChapter;
        setActiveChapter(nextChapter);
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
      window.removeEventListener("scroll", readScroll);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      envTex.dispose();
      groundTex.dispose();
      asphaltTex.dispose();
      shadowTex.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach((m) => m.dispose());
        }
      });
      renderer.dispose();
      canvas.remove();
    };
  }, []);

  const current = CHAPTERS[activeChapter];

  const jumpToChapter = (idx: number) => {
    setAutoFly(false);
    manualProgressRef.current = idx === 0 ? 0.06 : idx === 1 ? 0.48 : 0.92;
    setActiveChapter(idx);
  };

  return (
    <section
      ref={sectionRef}
      id="journey"
      aria-labelledby="journey-title"
      className="relative h-[240vh] min-h-[1550px] bg-[#09130f]"
    >
      <div className="sticky top-0 h-[100svh] min-h-[640px] w-full overflow-hidden">
        {webglUnavailable ? (
          <img
            src="/src/assets/images/hero_nagpur_estate_1795287010697.jpg"
            alt="Nagpur plotted sanctuary aerial concept"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover"
          />
        ) : (
          <div ref={canvasHostRef} className="absolute inset-0 h-full w-full" />
        )}

        {/* Measured directional gradient scrim for headline legibility (no backdrop-filter over WebGL) */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#080d0b]/75 via-transparent to-[#080d0b]/80"
        />

        {/* Top Minimal Title & Spec Strip */}
        <div className="pointer-events-none absolute inset-x-0 top-6 z-10 mx-auto flex max-w-7xl items-start justify-between px-6 md:px-10">
          <div className="max-w-xl">
            <p className="text-xs font-medium tracking-[0.14em] text-[#D4AF5F]">
              Spatial Estate Flythrough · Nagpur Corridors
            </p>
            <h2
              id="journey-title"
              className="mt-2 text-balance text-3xl font-normal tracking-tight text-[#F4F1E7] sm:text-5xl"
            >
              Experience the layout before your first footprint.
            </h2>
          </div>
          <div className="hidden text-right text-xs text-[#C7D0C8] md:block">
            <span className="font-mono-tabular text-[#D4AF5F]">
              {current.index} / 03
            </span>
            <p className="mt-1">{current.kicker}</p>
          </div>
        </div>

        {/* Collapsible Bottom Control Dock (Zero Viewport Center Occlusion, Solid rgba over WebGL) */}
        <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex flex-col items-center px-4">
          <button
            type="button"
            onClick={() => setDockCollapsed((c) => !c)}
            aria-expanded={!dockCollapsed}
            className="pointer-events-auto mb-2 inline-flex items-center gap-1.5 rounded-t-lg border border-b-0 border-[#D4AF5F]/25 bg-[#0c1612]/95 px-3.5 py-1.5 text-xs font-medium text-[#E5C77B] transition-colors hover:bg-[#13221c]"
          >
            {dockCollapsed ? (
              <>
                <ChevronUp className="h-3.5 w-3.5" />
                <span>Show Flight Controls</span>
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5" />
                <span>Hide Controls (H)</span>
              </>
            )}
          </button>

          <div
            className={`pointer-events-auto w-full max-w-[920px] rounded-xl border border-[#D4AF5F]/25 bg-[#0b1511]/95 px-5 py-4 text-[#F2EFE7] transition-all duration-300 ${
              dockCollapsed ? "pointer-events-none translate-y-28 opacity-0" : "translate-y-0 opacity-100"
            }`}
          >
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="max-w-lg">
                <div className="flex items-center gap-2 text-xs text-[#D4AF5F]">
                  <Compass className="h-3.5 w-3.5 shrink-0" />
                  <span className="font-medium">
                    {current.index}. {current.kicker}
                  </span>
                </div>
                <h3 className="mt-1 text-xl font-normal text-[#F7F4EC] md:text-2xl">
                  {current.title}
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-[#B9C5BC]">
                  {current.copy}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2 md:flex-col md:items-end">
                <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-[#080f0c] p-1">
                  {CHAPTERS.map((ch, idx) => (
                    <button
                      key={ch.index}
                      type="button"
                      onClick={() => jumpToChapter(idx)}
                      className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                        activeChapter === idx
                          ? "bg-[#D4AF5F] text-[#080C0A]"
                          : "text-[#B6C2B9] hover:text-white"
                      }`}
                    >
                      {ch.index}
                    </button>
                  ))}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setAutoFly((f) => !f)}
                    className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-white/15 bg-[#12201a] px-3 py-1.5 text-xs font-medium text-[#E8E4D8] transition-colors hover:border-[#D4AF5F]/50"
                  >
                    {autoFly ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                    <span>{autoFly ? "Pause Glide" : "Auto-Glide"}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => jumpToChapter(0)}
                    aria-label="Reset camera spline"
                    className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-white/15 bg-[#12201a] px-2.5 py-1.5 text-xs font-medium text-[#E8E4D8] transition-colors hover:border-[#D4AF5F]/50"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={onExploreEstates}
                    className="whitespace-nowrap rounded-lg bg-[#D4AF5F] px-3.5 py-1.5 text-xs font-semibold text-[#080C0A] transition-colors hover:bg-[#e4c67a]"
                  >
                    Inspect 4 Estates
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
