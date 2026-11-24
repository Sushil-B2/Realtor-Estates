import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { STATUS_META, type PlotCell, type ProjectLayout } from "../../data/projects";
import {
  buildCompassMonument,
  buildCompositeTree,
  buildEstateGateway,
  buildStreetLamp,
  createAsphaltTexture,
  createGroundTexture,
  createLabelSprite,
  createRoadLabelMesh,
  detectSoftwareWebGL,
  scheduleAfterDraw,
} from "./proceduralAssets";

export type MasterLayout3DHandle = {
  zoomBy: (factor: number) => void;
  resetView: () => void;
  focusPlot: (plotNo: string) => void;
  setPresetAngle: (preset: "aerial" | "isometric" | "top") => void;
};

type Props = {
  layout: ProjectLayout;
  selectedPlotNo: string | null;
  statusFilter: "all" | "available" | "reserved" | "sold";
  onSelectPlot: (plot: PlotCell) => void;
  getScrollProgress: () => number;
};

const YAW_BY_FACING: Record<string, number> = {
  N: Math.PI,
  S: 0,
  E: Math.PI / 2,
  W: -Math.PI / 2,
};

const DIR_BY_FACING: Record<string, [number, number]> = {
  N: [0, -1],
  S: [0, 1],
  E: [1, 0],
  W: [-1, 0],
};

// Reusable scratch vectors (zero per-frame GC)
const _basePos = new THREE.Vector3();
const _offset = new THREE.Vector3();
const _lookTarget = new THREE.Vector3();
const _currentLook = new THREE.Vector3();
const _yawAxis = new THREE.Vector3(0, 1, 0);
const _tempColor = new THREE.Color();

const MasterLayout3D = forwardRef<MasterLayout3DHandle, Props>(function MasterLayout3D(
  { layout, selectedPlotNo, statusFilter, onSelectPlot, getScrollProgress },
  ref,
) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const selectedRef = useRef<string | null>(selectedPlotNo);
  const filterRef = useRef(statusFilter);
  const onSelectRef = useRef(onSelectPlot);
  const [hoveredPlot, setHoveredPlot] = useState<PlotCell | null>(null);
  const [fallbackActive, setFallbackActive] = useState(false);

  selectedRef.current = selectedPlotNo;
  filterRef.current = statusFilter;
  onSelectRef.current = onSelectPlot;

  const rigRef = useRef({
    yaw: 0,
    targetYaw: 0,
    pitch: 1.0,
    targetPitch: 1.0,
    dist: 1.0,
    targetDist: 1.0,
    presetOverride: false,
  });

  useImperativeHandle(ref, () => ({
    zoomBy: (factor) => {
      rigRef.current.targetDist = THREE.MathUtils.clamp(rigRef.current.targetDist * factor, 0.38, 1.85);
    },
    resetView: () => {
      rigRef.current.targetYaw = 0;
      rigRef.current.targetPitch = 1.0;
      rigRef.current.targetDist = 1.0;
      rigRef.current.presetOverride = false;
    },
    focusPlot: () => {
      rigRef.current.targetDist = 0.65;
    },
    setPresetAngle: (preset) => {
      rigRef.current.presetOverride = true;
      if (preset === "aerial") {
        rigRef.current.targetYaw = 0.35;
        rigRef.current.targetPitch = 1.05;
        rigRef.current.targetDist = 1.08;
      } else if (preset === "isometric") {
        rigRef.current.targetYaw = -0.65;
        rigRef.current.targetPitch = 0.72;
        rigRef.current.targetDist = 0.85;
      } else {
        rigRef.current.targetYaw = 0;
        rigRef.current.targetPitch = 1.78;
        rigRef.current.targetDist = 0.96;
      }
    },
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const isSoftwareGl = detectSoftwareWebGL();
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: !isSoftwareGl,
        powerPreference: "high-performance",
      });
    } catch {
      setFallbackActive(true);
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
    canvas.className = "block h-full w-full cursor-grab active:cursor-grabbing";
    canvas.style.touchAction = "pan-y";
    host.appendChild(canvas);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x09130f);
    scene.fog = new THREE.FogExp2(0x09130f, 0.0024);

    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    pmrem.dispose();

    const camera = new THREE.PerspectiveCamera(42, 1, 0.5, 1400);

    // 3-point warm/cool studio outdoor rig
    scene.add(new THREE.HemisphereLight(0xd7e9dc, 0x16241d, 1.45));

    const sun = new THREE.DirectionalLight(0xfbe0ac, 2.55);
    sun.position.set(-70, 125, 78);
    if (!isSoftwareGl) {
      sun.castShadow = true;
      sun.shadow.mapSize.set(1024, 1024);
      sun.shadow.camera.left = -120;
      sun.shadow.camera.right = 120;
      sun.shadow.camera.top = 120;
      sun.shadow.camera.bottom = -120;
      sun.shadow.camera.near = 10;
      sun.shadow.camera.far = 320;
      sun.shadow.bias = -0.0005;
      sun.shadow.normalBias = 0.02;
    }
    scene.add(sun);

    const rim = new THREE.DirectionalLight(0x8ec3a8, 0.9);
    rim.position.set(85, 50, -90);
    scene.add(rim);

    const { bounds, plots, roads, zones } = layout;
    const cx = bounds.x;
    const cz = bounds.z;
    const minX = cx - bounds.w / 2;
    const maxX = cx + bounds.w / 2;
    const minZ = cz - bounds.d / 2;
    const maxZ = cz + bounds.d / 2;
    const radius = Math.max(bounds.w, bounds.d) * 0.58;

    // Extended landscape horizon plane
    const groundTex = createGroundTexture(renderer, "#12241b", "#1f3c2d", 24);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(bounds.w + 420, bounds.d + 420),
      new THREE.MeshStandardMaterial({ map: groundTex, roughness: 0.96, metalness: 0.02 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(cx, 0, cz);
    ground.receiveShadow = true;
    scene.add(ground);

    // Subtle brass perimeter line framing the estate boundary
    const boundaryPts = [
      new THREE.Vector3(minX - 3, 0.25, minZ - 3),
      new THREE.Vector3(maxX + 3, 0.25, minZ - 3),
      new THREE.Vector3(maxX + 3, 0.25, maxZ + 3),
      new THREE.Vector3(minX - 3, 0.25, maxZ + 3),
      new THREE.Vector3(minX - 3, 0.25, minZ - 3),
    ];
    scene.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(boundaryPts),
        new THREE.LineBasicMaterial({ color: 0xd4af5f, transparent: true, opacity: 0.55 }),
      ),
    );

    // Roads (supports angled blueprint geometry via rotDeg)
    const asphaltTex = createAsphaltTexture(renderer);
    const roadMat = new THREE.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.86,
      metalness: 0.06,
    });
    for (const road of roads) {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(road.w, 0.26, road.d), roadMat);
      mesh.position.set(road.x, 0.13, road.z);
      if (road.rotDeg) mesh.rotation.y = -THREE.MathUtils.degToRad(road.rotDeg);
      mesh.receiveShadow = true;
      scene.add(mesh);

      const isWideArterial = /12\.00|18\.00|30\.00/.test(road.label);
      const label = createRoadLabelMesh(
        renderer,
        road.label.toUpperCase(),
        road.w,
        road.d,
        road.orient,
        {
          rotDeg: road.rotDeg,
          isWideArterial,
        },
      );
      label.position.set(road.x, 0.34, road.z);
      scene.add(label);
    }

    // Open parks, amenities & green buffers
    const zoneColors: Record<string, number> = {
      open: 0x2a573b,
      amenity: 0x356445,
      nala: 0x1e4358,
      vacant: 0x363e37,
    };
    for (const zone of zones) {
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(zone.w, 0.22, zone.d),
        new THREE.MeshStandardMaterial({
          color: zoneColors[zone.kind] ?? 0x2a573b,
          roughness: 0.9,
          metalness: 0.02,
        }),
      );
      mesh.position.set(zone.x, 0.11, zone.z);
      if (zone.rotDeg) mesh.rotation.y = -THREE.MathUtils.degToRad(zone.rotDeg);
      mesh.receiveShadow = true;
      scene.add(mesh);

      if (zone.w >= 18) {
        const lbl = createLabelSprite(renderer, zone.label, Math.min(zone.w * 0.72, 34), 5.2, {
          accent: true,
          renderOrder: 14,
        });
        lbl.position.set(zone.x, 4.2, zone.z);
        scene.add(lbl);
      }

      // Populate botanical composite trees inside open/amenity zones
      if (zone.kind === "open" || zone.kind === "amenity") {
        const count = Math.max(2, Math.min(5, Math.floor(zone.w / 12)));
        for (let t = 0; t < count; t++) {
          const tree = buildCompositeTree(1.4, (t % 3) - 1);
          const ox = -zone.w * 0.36 + (zone.w * 0.72 * t) / Math.max(1, count - 1);
          const oz = (t % 2 === 0 ? -1 : 1) * Math.min(zone.d * 0.24, 3);
          tree.position.set(zone.x + ox, 0.22, zone.z + oz);
          scene.add(tree);
        }
      }
    }

    // Grand entrance gate + perimeter botanical grove + streetlamps
    const gate = buildEstateGateway(1.4);
    gate.position.set(cx, 0, maxZ + 3);
    scene.add(gate);

    for (let i = 0; i < 6; i++) {
      const lamp = buildStreetLamp(1.3);
      lamp.position.set(minX + 14 + i * ((bounds.w - 28) / 5), 0, roads[0]?.z ? roads[0].z + 5.2 : cz);
      scene.add(lamp);
    }

    // Natural perimeter tree grove
    for (let i = 0; i < 14; i++) {
      const angle = (i / 14) * Math.PI * 2;
      const tx = cx + Math.cos(angle) * (bounds.w * 0.57);
      const tz = cz + Math.sin(angle) * (bounds.d * 0.57);
      const tree = buildCompositeTree(1.75 + (i % 3) * 0.25, (i % 3) - 1);
      tree.position.set(tx, 0, tz);
      scene.add(tree);
    }

    // True-North 3D Brass Compass Monument at North Edge
    const compass = buildCompassMonument(6.2);
    compass.position.set(cx, 0.1, minZ - 14);
    scene.add(compass);
    const northSprite = createLabelSprite(renderer, "TRUE NORTH", 16, 5.2, { accent: true });
    northSprite.position.set(cx, 3.6, minZ - 19);
    scene.add(northSprite);

    // Instanced sculpted plot parcels + sold-out miniature villa footprints + facing arrows
    const plotGeo = new THREE.BoxGeometry(1, 1, 1);
    const plotMat = new THREE.MeshStandardMaterial({ roughness: 0.62, metalness: 0.08 });
    const plotsMesh = new THREE.InstancedMesh(plotGeo, plotMat, Math.max(plots.length, 1));
    plotsMesh.castShadow = !isSoftwareGl;
    plotsMesh.receiveShadow = true;

    const soldPlots = plots.filter((pl) => pl.status === "sold");
    const soldBodyGeo = new THREE.BoxGeometry(1, 1, 1);
    const soldBodyMat = new THREE.MeshStandardMaterial({
      color: 0xd9cfc0,
      roughness: 0.7,
      metalness: 0.08,
    });
    const soldRoofGeo = new THREE.BoxGeometry(1, 1, 1);
    const soldRoofMat = new THREE.MeshStandardMaterial({
      color: 0x3b2c25,
      roughness: 0.55,
      metalness: 0.22,
    });
    const soldBodiesMesh = new THREE.InstancedMesh(soldBodyGeo, soldBodyMat, Math.max(soldPlots.length, 1));
    const soldRoofsMesh = new THREE.InstancedMesh(soldRoofGeo, soldRoofMat, Math.max(soldPlots.length, 1));
    soldBodiesMesh.castShadow = !isSoftwareGl;
    soldBodiesMesh.receiveShadow = true;

    const arrowGeo = new THREE.ConeGeometry(0.7, 1.85, 4);
    const arrowMat = new THREE.MeshStandardMaterial({
      color: 0x0b1610,
      roughness: 0.4,
      metalness: 0.4,
    });
    const arrowsMesh = new THREE.InstancedMesh(arrowGeo, arrowMat, Math.max(plots.length, 1));

    const m4 = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const qRx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
    const qYaw = new THREE.Quaternion();
    const p = new THREE.Vector3();
    const s = new THREE.Vector3();

    const baseColors: THREE.Color[] = [];
    const plotLabels: THREE.Sprite[] = [];
    const isDenseSheet = plots.length > 120;
    let soldIdx = 0;

    plots.forEach((plot, i) => {
      const h = plot.status === "available" ? 1.45 : 0.9;
      const rotRad = -THREE.MathUtils.degToRad(plot.rotDeg ?? 0);
      p.set(plot.x, h / 2 + 0.06, plot.z);
      s.set(plot.w * 0.92, h, plot.d * 0.92);
      q.setFromAxisAngle(_yawAxis, rotRad);
      m4.compose(p, q, s);
      plotsMesh.setMatrixAt(i, m4);

      const c = new THREE.Color(STATUS_META[plot.status].hex);
      plotsMesh.setColorAt(i, c);
      baseColors.push(c);

      // Add miniature 3D allotted residence massing directly atop SOLD plots
      if (plot.status === "sold") {
        const bw = Math.max(plot.w * 0.52, 3.2);
        const bd = Math.max(plot.d * 0.48, 3.0);
        const bh = 1.95;
        p.set(plot.x, h + bh / 2 + 0.06, plot.z);
        s.set(bw, bh, bd);
        m4.compose(p, q, s);
        soldBodiesMesh.setMatrixAt(soldIdx, m4);

        p.set(plot.x, h + bh + 0.24, plot.z);
        s.set(bw * 1.12, 0.36, bd * 1.12);
        m4.compose(p, q, s);
        soldRoofsMesh.setMatrixAt(soldIdx, m4);
        soldIdx++;
      }

      const yaw = (YAW_BY_FACING[plot.faces] ?? 0) + rotRad;
      qYaw.setFromAxisAngle(_yawAxis, yaw);
      const qa = qYaw.clone().multiply(qRx);
      const [ldx, ldz] = DIR_BY_FACING[plot.faces] ?? [0, 1];
      const cosR = Math.cos(rotRad);
      const sinR = Math.sin(rotRad);
      const ox = ldx * (plot.w / 2 - 1.25);
      const oz = ldz * (plot.d / 2 - 1.25);
      const rdx = ox * cosR + oz * sinR;
      const rdz = -ox * sinR + oz * cosR;
      p.set(plot.x + rdx, h + 0.42, plot.z + rdz);
      s.set(0.95, 0.95, 0.95);
      m4.compose(p, qa, s);
      arrowsMesh.setMatrixAt(i, m4);

      const lblScaleX = Math.min(plot.w * (isDenseSheet ? 0.62 : 0.72), isDenseSheet ? 7.2 : 9.8);
      const lblScaleY = isDenseSheet ? 3.2 : 4.2;
      const lbl = createLabelSprite(renderer, plot.displayNo ?? plot.no, lblScaleX, lblScaleY);
      lbl.position.set(plot.x, plot.status === "sold" ? h + 3.15 : h + 1.15, plot.z);
      scene.add(lbl);
      plotLabels.push(lbl);
    });
    plotsMesh.instanceMatrix.needsUpdate = true;
    if (plotsMesh.instanceColor) plotsMesh.instanceColor.needsUpdate = true;
    plotsMesh.computeBoundingSphere();
    arrowsMesh.instanceMatrix.needsUpdate = true;
    arrowsMesh.computeBoundingSphere();
    scene.add(plotsMesh);
    scene.add(arrowsMesh);
    if (soldPlots.length > 0) {
      soldBodiesMesh.instanceMatrix.needsUpdate = true;
      soldRoofsMesh.instanceMatrix.needsUpdate = true;
      scene.add(soldBodiesMesh);
      scene.add(soldRoofsMesh);
    }

    // Highlight gold selection outline
    const selectOutline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: 0xf4d684 }),
    );
    selectOutline.visible = false;
    scene.add(selectOutline);

    // Spline camera curve circling the estate
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(cx - radius * 1.15, radius * 0.88, cz + radius * 1.05),
      new THREE.Vector3(cx - radius * 0.55, radius * 0.55, cz + radius * 0.62),
      new THREE.Vector3(cx + radius * 0.08, radius * 0.46, cz + radius * 0.35),
      new THREE.Vector3(cx + radius * 0.68, radius * 0.58, cz - radius * 0.32),
      new THREE.Vector3(cx + radius * 1.05, radius * 0.88, cz - radius * 0.88),
    ]);
    const center = new THREE.Vector3(cx, 0, cz);

    if (!isSoftwareGl) {
      renderer.compile(scene, camera);
    }

    // Raycasting & 360° pointer drag orbit
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2(-99, -99);
    const drag = { active: false, moved: 0, x: 0, y: 0 };
    let hoveredIdx = -1;
    let lastHoverRaycast = 0;

    const updatePointer = (ev: PointerEvent | WheelEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const onPointerDown = (ev: PointerEvent) => {
      updatePointer(ev);
      drag.active = true;
      drag.moved = 0;
      drag.x = ev.clientX;
      drag.y = ev.clientY;
      try {
        canvas.setPointerCapture(ev.pointerId);
      } catch {
        // ignore
      }
    };

    const onPointerMove = (ev: PointerEvent) => {
      updatePointer(ev);
      if (drag.active) {
        const dx = ev.clientX - drag.x;
        const dy = ev.clientY - drag.y;
        drag.moved += Math.abs(dx) + Math.abs(dy);
        drag.x = ev.clientX;
        drag.y = ev.clientY;
        rigRef.current.targetYaw -= dx * 0.0055;
        rigRef.current.targetPitch = THREE.MathUtils.clamp(
          rigRef.current.targetPitch + dy * 0.004,
          0.42,
          1.85,
        );
        return;
      }

      const now = performance.now();
      if (now - lastHoverRaycast < 45) return;
      lastHoverRaycast = now;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(plotsMesh, false);
      const hitId = hits.length > 0 && hits[0].instanceId !== undefined ? hits[0].instanceId : -1;
      if (hitId !== hoveredIdx) {
        hoveredIdx = hitId;
        canvas.style.cursor = hitId >= 0 ? "pointer" : "grab";
        setHoveredPlot(hitId >= 0 ? plots[hitId] ?? null : null);
      }
    };

    const onPointerUp = (ev: PointerEvent) => {
      if (drag.active && drag.moved < 8) {
        updatePointer(ev);
        raycaster.setFromCamera(pointer, camera);
        const hits = raycaster.intersectObject(plotsMesh, false);
        if (hits.length > 0 && hits[0].instanceId !== undefined) {
          const hitPlot = plots[hits[0].instanceId];
          if (hitPlot) onSelectRef.current(hitPlot);
        }
      }
      drag.active = false;
      try {
        canvas.releasePointerCapture(ev.pointerId);
      } catch {
        // ignore
      }
    };

    const onWheel = (ev: WheelEvent) => {
      if (!ev.ctrlKey && !ev.metaKey) return; // Allow page scroll unless zooming intentionally
      ev.preventDefault();
      rigRef.current.targetDist = THREE.MathUtils.clamp(
        rigRef.current.targetDist * (ev.deltaY > 0 ? 1.08 : 0.92),
        0.38,
        1.85,
      );
    };

    const onContextLost = (ev: Event) => {
      ev.preventDefault();
      setFallbackActive(true);
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("webglcontextlost", onContextLost);

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

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const clock = new THREE.Clock();
    let easedProgress = 0.14;
    let frameId = 0;
    let disposed = false;
    let isVisible = true;
    let loopRunning = false;
    _currentLook.copy(center);

    const startLoop = () => {
      if (disposed || loopRunning || !isVisible || document.hidden) return;
      loopRunning = true;
      clock.getDelta();
      frameId = window.requestAnimationFrame(tick);
    };

    const stopLoop = () => {
      loopRunning = false;
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(frameId);
    };

    const tick = () => {
      if (disposed || !isVisible || document.hidden) {
        loopRunning = false;
        return;
      }
      const dt = Math.min(clock.getDelta(), 0.1);

      const targetP = rigRef.current.presetOverride
        ? 0.25
        : THREE.MathUtils.clamp(getScrollProgress(), 0, 1);
      easedProgress = reducedMotion ? targetP : THREE.MathUtils.damp(easedProgress, targetP, 5, dt);

      rigRef.current.yaw = reducedMotion
        ? rigRef.current.targetYaw
        : THREE.MathUtils.damp(rigRef.current.yaw, rigRef.current.targetYaw, 6, dt);
      rigRef.current.pitch = reducedMotion
        ? rigRef.current.targetPitch
        : THREE.MathUtils.damp(rigRef.current.pitch, rigRef.current.targetPitch, 6, dt);
      rigRef.current.dist = reducedMotion
        ? rigRef.current.targetDist
        : THREE.MathUtils.damp(rigRef.current.dist, rigRef.current.targetDist, 6, dt);

      curve.getPoint(easedProgress, _basePos);
      _offset.copy(_basePos).sub(center);
      _offset.applyAxisAngle(_yawAxis, rigRef.current.yaw);
      camera.position.set(
        center.x + _offset.x * rigRef.current.dist,
        Math.max(12, _offset.y * rigRef.current.pitch * rigRef.current.dist),
        center.z + _offset.z * rigRef.current.dist,
      );

      const sel = selectedRef.current ? plots.find((pl) => pl.no === selectedRef.current) : undefined;
      if (sel) {
        _lookTarget.set(sel.x, 0.5, sel.z).lerp(center, 0.42);
      } else {
        _lookTarget.copy(center);
      }
      _currentLook.lerp(_lookTarget, reducedMotion ? 1 : 1 - Math.exp(-6 * dt));
      camera.lookAt(_currentLook);

      // Update colors cleanly based on status filter & hover state
      const activeFilter = filterRef.current;
      let colorDirty = false;
      for (let i = 0; i < plots.length; i++) {
        const pl = plots[i];
        const muted = activeFilter !== "all" && pl.status !== activeFilter;
        _tempColor.copy(baseColors[i]);
        if (muted) {
          _tempColor.multiplyScalar(0.26);
        } else if (i === hoveredIdx || pl.no === selectedRef.current) {
          _tempColor.multiplyScalar(1.28);
        }
        plotsMesh.setColorAt(i, _tempColor);
        colorDirty = true;
      }
      if (colorDirty && plotsMesh.instanceColor) {
        plotsMesh.instanceColor.needsUpdate = true;
      }

      // Selected wireframe gold halo
      if (sel) {
        selectOutline.visible = true;
        selectOutline.position.set(sel.x, 1.0, sel.z);
        selectOutline.rotation.y = -THREE.MathUtils.degToRad(sel.rotDeg ?? 0);
        selectOutline.scale.set(sel.w * 0.98, 2.0, sel.d * 0.98);
      } else {
        selectOutline.visible = false;
      }

      // Cull distant plot labels for visual clarity
      const maxLabelDistSq = (radius * 2.4) ** 2;
      for (let i = 0; i < plotLabels.length; i++) {
        const sp = plotLabels[i];
        sp.visible = sp.position.distanceToSquared(camera.position) < maxLabelDistSq;
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

    const io = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        isVisible = Boolean(entry?.isIntersecting);
        if (isVisible) startLoop();
        else stopLoop();
      },
      { rootMargin: "200px 0px" },
    );
    io.observe(host);

    const onVisibilityChange = () => {
      if (document.hidden) stopLoop();
      else startLoop();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    startLoop();

    return () => {
      disposed = true;
      stopLoop();
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      envTex.dispose();
      groundTex.dispose();
      asphaltTex.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.Line || obj instanceof THREE.LineSegments || obj instanceof THREE.Sprite) {
          obj.geometry.dispose();
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach((mat) => {
            if ("map" in mat && mat.map instanceof THREE.Texture) mat.map.dispose();
            mat.dispose();
          });
        }
      });
      renderer.dispose();
      canvas.remove();
    };
  }, [layout]);

  return (
    <div className="relative h-full w-full">
      {fallbackActive ? (
        <div className="flex h-full w-full items-center justify-center bg-[#0c1712] text-sm text-[#B7C3BA]">
          3D WebGL context unavailable — use the interactive 2D layout sheet below to inspect any plot.
        </div>
      ) : (
        <div ref={hostRef} className="h-full w-full" />
      )}

      {/* Subtle hover readout in top-right (non-obstructive, solid rgba, no backdrop-filter) */}
      {hoveredPlot ? (
        <div className="pointer-events-none absolute top-20 right-6 z-20 rounded-lg border border-[#D4AF5F]/35 bg-[#0a1410]/95 px-4 py-2.5 text-xs text-[#F2EFE7]">
          <div className="flex items-center justify-between gap-4">
            <span className="font-semibold text-[#E5C77B]">
              Plot {hoveredPlot.displayNo ?? hoveredPlot.no}
              {hoveredPlot.wing ? ` · ${hoveredPlot.wing}` : ""}
            </span>
            <span className="text-[11px] text-[#B7C3BA]">{STATUS_META[hoveredPlot.status].label}</span>
          </div>
          <p className="font-mono-tabular mt-1 text-[11px] text-[#D7DDD8]">
            {hoveredPlot.w}m × {hoveredPlot.d}m · {hoveredPlot.areaSqFt.toLocaleString("en-IN")} sq.ft
          </p>
          <p className="mt-0.5 text-[11px] text-[#9EB0A3]">Click to open 360° Land View</p>
        </div>
      ) : null}
    </div>
  );
});

export default MasterLayout3D;
