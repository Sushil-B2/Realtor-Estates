"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import * as THREE from "three";
import { STATUS_META, type ProjectLayout } from "@/lib/projects";

export type SceneHandle = {
  zoomBy: (factor: number) => void;
  resetView: () => void;
};

type Props = {
  layout: ProjectLayout;
  selected: string | null;
  onSelect: (no: string | null) => void;
  getProgress: () => number;
};

const STATUS_COLOR: Record<string, THREE.Color> = {
  available: new THREE.Color(STATUS_META.available.color),
  reserved: new THREE.Color(STATUS_META.reserved.color),
  sold: new THREE.Color(STATUS_META.sold.color),
};

const YAW_BY_FACING: Record<string, number> = { N: Math.PI, S: 0, E: Math.PI / 2, W: -Math.PI / 2 };
const DIR_BY_FACING: Record<string, [number, number]> = { N: [0, -1], S: [0, 1], E: [1, 0], W: [-1, 0] };

function textTexture(text: string, rotate = false): THREE.CanvasTexture {
  const w = 256;
  const h = 128;
  const canvas = document.createElement("canvas");
  canvas.width = rotate ? h : w;
  canvas.height = rotate ? w : h;
  const ctx = canvas.getContext("2d");
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  if (!ctx) return tex;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (rotate) {
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.translate(-canvas.height / 2, -canvas.width / 2);
  }
  const dw = rotate ? h : w;
  const dh = rotate ? w : h;
  ctx.font = `700 ${rotate ? 52 : 56}px Arial, Helvetica, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 12;
  ctx.strokeStyle = "rgba(8,22,15,0.92)";
  ctx.strokeText(text, dw / 2, dh / 2 + 3);
  ctx.fillStyle = "#f7f8ee";
  ctx.fillText(text, dw / 2, dh / 2 + 3);
  tex.needsUpdate = true;
  return tex;
}

function makeLabel(text: string, scaleX: number, scaleY: number, rotate = false): THREE.Sprite {
  const material = new THREE.SpriteMaterial({ map: textTexture(text, rotate), transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(scaleX, scaleY, 1);
  return sprite;
}

const SceneCanvas = forwardRef<SceneHandle, Props>(function SceneCanvas({ layout, selected, onSelect, getProgress }, ref) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const selectedRef = useRef<string | null>(selected);
  const onSelectRef = useRef(onSelect);
  selectedRef.current = selected;
  onSelectRef.current = onSelect;

  useImperativeHandle(ref, () => ({
    zoomBy: (factor) => {
      rigRef.current.dist = THREE.MathUtils.clamp(rigRef.current.dist * factor, 0.35, 2.1);
    },
    resetView: () => {
      rigRef.current.yaw = 0;
      rigRef.current.pitch = 1;
      rigRef.current.dist = 1;
    },
  }));

  const rigRef = useRef({ yaw: 0, pitch: 1, dist: 1 });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        alpha: false,
        antialias: window.innerWidth > 640,
        powerPreference: "low-power",
      });
    } catch {
      return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x102b22, 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    host.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.className = "scene-canvas__webgl";

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x102b22, 0.0011);

    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 2000);

    scene.add(new THREE.HemisphereLight(0xdfeeda, 0x27402f, 2.1));
    const sun = new THREE.DirectionalLight(0xf6d9a0, 2.6);
    sun.position.set(-60, 110, 70);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xa9cdb2, 0.9);
    fill.position.set(80, 50, -90);
    scene.add(fill);

    const { bounds, plots, roads, zones } = layout;
    const minX = bounds.x - bounds.w / 2;
    const minZ = bounds.z - bounds.d / 2;
    const maxX = bounds.x + bounds.w / 2;
    const maxZ = bounds.z + bounds.d / 2;
    const cx = bounds.x;
    const cz = bounds.z;
    const radius = Math.max(bounds.w, bounds.d) / 2;

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(bounds.w + 160, bounds.d + 160),
      new THREE.MeshStandardMaterial({ color: 0x1c382b, roughness: 1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(cx, -0.05, cz);
    scene.add(ground);

    const boundaryPoints = [
      new THREE.Vector3(minX - 5, 0.12, minZ - 5),
      new THREE.Vector3(maxX + 5, 0.12, minZ - 5),
      new THREE.Vector3(maxX + 5, 0.12, maxZ + 5),
      new THREE.Vector3(minX - 5, 0.12, maxZ + 5),
      new THREE.Vector3(minX - 5, 0.12, minZ - 5),
    ];
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(boundaryPoints), new THREE.LineBasicMaterial({ color: 0x8fae90, transparent: true, opacity: 0.7 })));

    // Roads
    const roadMaterial = new THREE.MeshStandardMaterial({ color: 0x2e3833, roughness: 0.95 });
    for (const road of roads) {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(road.w, 0.16, road.d), roadMaterial);
      mesh.position.set(road.x, 0.08, road.z);
      scene.add(mesh);
      const label = makeLabel(road.label, road.orient === "h" ? 24 : 7, road.orient === "h" ? 7 : 24, road.orient === "v");
      label.position.set(road.x, 0.24, road.z);
      scene.add(label);
    }

    // Zones
    const zoneColors: Record<string, number> = { open: 0x3f5c3d, amenity: 0x4d704a, nala: 0x27495e, vacant: 0x3a4038 };
    for (const zone of zones) {
      const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(zone.w, zone.d),
        new THREE.MeshStandardMaterial({ color: zoneColors[zone.kind] ?? 0x3f5c3d, roughness: 1, transparent: true, opacity: 0.9 }),
      );
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(zone.x, 0.06, zone.z);
      scene.add(mesh);
      if (zone.w > 18) {
        const label = makeLabel(zone.label, Math.min(zone.w * 0.7, 46), 5.5);
        label.position.set(zone.x, 0.14, zone.z);
        scene.add(label);
      }
    }

    // Plots (instanced) + facing arrows (instanced) + labels (sprites, culled by distance)
    const plotGeometry = new THREE.BoxGeometry(1, 1, 1);
    const plotMaterial = new THREE.MeshStandardMaterial({ roughness: 0.86, metalness: 0.02 });
    const plotsMesh = new THREE.InstancedMesh(plotGeometry, plotMaterial, Math.max(plots.length, 1));
    const arrowGeometry = new THREE.ConeGeometry(0.62, 1.7, 4);
    const arrowMaterial = new THREE.MeshBasicMaterial({ color: 0x12261c });
    const arrowsMesh = new THREE.InstancedMesh(arrowGeometry, arrowMaterial, Math.max(plots.length, 1));

    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const qRx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
    const qYaw = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    const scl = new THREE.Vector3();
    const baseColors: THREE.Color[] = [];
    const labelSprites: THREE.Sprite[] = [];

    plots.forEach((plot, i) => {
      pos.set(plot.x, 0.4, plot.z);
      scl.set(plot.w * 0.94, 0.8, plot.d * 0.94);
      m.compose(pos, q, scl);
      plotsMesh.setMatrixAt(i, m);
      const color = STATUS_COLOR[plot.status] ?? STATUS_COLOR.available;
      plotsMesh.setColorAt(i, color);
      baseColors.push(color.clone());

      const yaw = YAW_BY_FACING[plot.faces] ?? 0;
      qYaw.setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
      const qa = qYaw.clone().multiply(qRx);
      const [dx, dz] = DIR_BY_FACING[plot.faces] ?? [0, 1];
      pos.set(plot.x + dx * (plot.w / 2 - 0.9), 1.0, plot.z + dz * (plot.d / 2 - 0.9));
      const as = THREE.MathUtils.clamp(Math.min(plot.w, plot.d) * 0.16, 0.7, 1.7);
      scl.set(as, as, as);
      m.compose(pos, qa, scl);
      arrowsMesh.setMatrixAt(i, m);

      const label = makeLabel(plot.no, Math.min(plot.w * 0.92, 13), Math.min(plot.w * 0.92, 13) / 2);
      label.position.set(plot.x, 1.15, plot.z);
      scene.add(label);
      labelSprites.push(label);
    });
    plotsMesh.instanceMatrix.needsUpdate = true;
    if (plotsMesh.instanceColor) plotsMesh.instanceColor.needsUpdate = true;
    arrowsMesh.instanceMatrix.needsUpdate = true;
    scene.add(plotsMesh);
    scene.add(arrowsMesh);

    // Selected outline
    const selectOutline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: 0xd8ec79 }),
    );
    selectOutline.visible = false;
    scene.add(selectOutline);

    // North arrow
    const northGroup = new THREE.Group();
    const northCone = new THREE.Mesh(new THREE.ConeGeometry(1.5, 3.4, 4), new THREE.MeshBasicMaterial({ color: 0xd8ec79 }));
    northCone.rotation.x = -Math.PI / 2;
    northCone.position.y = 1.7;
    const northRing = new THREE.Mesh(new THREE.TorusGeometry(3.2, 0.16, 8, 40), new THREE.MeshBasicMaterial({ color: 0xd8ec79, transparent: true, opacity: 0.8 }));
    northRing.rotation.x = -Math.PI / 2;
    northRing.position.y = 0.3;
    const northLabel = makeLabel("N", 6, 6);
    northLabel.position.y = 5.4;
    northGroup.add(northCone, northRing, northLabel);
    northGroup.position.set(cx, 0, minZ - 9);
    scene.add(northGroup);

    // Camera spline around the layout
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(cx - radius * 1.15, radius * 0.9, cz + radius * 1.05),
      new THREE.Vector3(cx - radius * 0.5, radius * 0.48, cz + radius * 0.38),
      new THREE.Vector3(cx + radius * 0.05, radius * 0.34, cz - radius * 0.05),
      new THREE.Vector3(cx + radius * 0.55, radius * 0.52, cz - radius * 0.45),
      new THREE.Vector3(cx + radius * 1.0, radius * 0.95, cz - radius * 0.85),
    ]);
    const center = new THREE.Vector3(cx, 0, cz);
    const yawAxis = new THREE.Vector3(0, 1, 0);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const drag = { active: false, moved: 0, x: 0, y: 0 };
    let hovered = -1;

    const setPointer = (event: PointerEvent | WheelEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const pick = (event: PointerEvent) => {
      setPointer(event);
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(plotsMesh, false);
      if (hits.length > 0 && hits[0].instanceId !== undefined) {
        onSelectRef.current(plots[hits[0].instanceId]?.no ?? null);
      } else {
        onSelectRef.current(null);
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      drag.active = true;
      drag.moved = 0;
      drag.x = event.clientX;
      drag.y = event.clientY;
      renderer.domElement.setPointerCapture(event.pointerId);
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!drag.active) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      drag.x = event.clientX;
      drag.y = event.clientY;
      rigRef.current.yaw -= dx * 0.0052;
      rigRef.current.pitch = THREE.MathUtils.clamp(rigRef.current.pitch + dy * 0.0038, 0.45, 1.8);
    };
    const onPointerUp = (event: PointerEvent) => {
      if (drag.active && drag.moved < 7) pick(event);
      drag.active = false;
      try {
        renderer.domElement.releasePointerCapture(event.pointerId);
      } catch {
        // ignore
      }
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      rigRef.current.dist = THREE.MathUtils.clamp(
        rigRef.current.dist * (event.deltaY > 0 ? 1.09 : 0.92),
        0.35,
        2.1,
      );
    };

    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerup", onPointerUp);
    renderer.domElement.addEventListener("wheel", onWheel, { passive: false });

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let eased = 0.12;
    let frame = 0;
    const basePos = new THREE.Vector3();
    const offset = new THREE.Vector3();
    const lookTarget = new THREE.Vector3();

    const renderFrame = () => {
      const target = THREE.MathUtils.clamp(getProgress(), 0, 1);
      eased = reduced ? target : eased + (target - eased) * 0.08;
      curve.getPoint(eased, basePos);
      offset.copy(basePos).sub(center);
      offset.applyAxisAngle(yawAxis, rigRef.current.yaw);
      camera.position.set(
        center.x + offset.x * rigRef.current.dist,
        Math.max(7, offset.y * rigRef.current.pitch * rigRef.current.dist),
        center.z + offset.z * rigRef.current.dist,
      );

      const sel = selectedRef.current ? plots.find((p) => p.no === selectedRef.current) : undefined;
      if (sel) {
        lookTarget.set(sel.x, 0, sel.z).lerp(center, 0.62);
      } else {
        lookTarget.copy(center);
      }
      camera.lookAt(lookTarget);

      // Cull plot labels when far away
      const cullDist = radius * 2.1;
      for (const sprite of labelSprites) {
        sprite.visible = sprite.position.distanceToSquared(camera.position) < cullDist * cullDist;
      }

      // Hover highlight
      if (!drag.active) {
        raycaster.setFromCamera(pointer, camera);
        const hits = raycaster.intersectObject(plotsMesh, false);
        const id = hits.length > 0 ? (hits[0].instanceId ?? -1) : -1;
        if (id !== hovered) {
          if (hovered >= 0) plotsMesh.setColorAt(hovered, baseColors[hovered]);
          if (id >= 0) {
            const c = baseColors[id].clone().multiplyScalar(1.22);
            plotsMesh.setColorAt(id, c);
          }
          if (plotsMesh.instanceColor) plotsMesh.instanceColor.needsUpdate = true;
          hovered = id;
        }
      }

      // Selected outline
      if (sel) {
        selectOutline.visible = true;
        selectOutline.position.set(sel.x, 0.62, sel.z);
        selectOutline.scale.set(sel.w + 1.1, 1.5, sel.d + 1.1);
      } else {
        selectOutline.visible = false;
      }

      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(renderFrame);
    };
    frame = window.requestAnimationFrame(renderFrame);

    return () => {
      window.cancelAnimationFrame(frame);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("wheel", onWheel);
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.LineSegments || object instanceof THREE.Sprite) {
          object.geometry.dispose();
          const material = object.material as THREE.Material | THREE.Material[];
          const mats = Array.isArray(material) ? material : [material];
          for (const mat of mats) {
            const map = (mat as THREE.SpriteMaterial).map;
            if (map) map.dispose();
            mat.dispose();
          }
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout]);

  return <div className="scene-canvas" ref={hostRef} />;
});

export default SceneCanvas;
