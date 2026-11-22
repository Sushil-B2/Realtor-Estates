"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

const chapters = [
  {
    label: "ACCESS",
    title: "Begin with the way in.",
    copy: "Look beyond a pin on a map. Walk the approach, read the actual road widths on the approved plan and see how the internal streets meet the ground.",
  },
  {
    label: "LAYOUT",
    title: "See the whole picture.",
    copy: "A plot makes more sense in context: its edges, the road beside it, neighbouring spaces and the wider layout—not just a square-foot number.",
  },
  {
    label: "YOUR NEXT STEP",
    title: "Decide with your eyes open.",
    copy: "Bring the checklist. Ask for the current plan and documents. Visit in person. The right decision should still feel right after the questions are answered.",
  },
];

export default function ImmersiveJourney() {
  const sectionRef = useRef<HTMLElement | null>(null);
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const [activeChapter, setActiveChapter] = useState(0);

  useEffect(() => {
    const section = sectionRef.current;
    const host = canvasHostRef.current;
    if (!section || !host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: window.innerWidth > 640,
        powerPreference: "low-power",
      });
    } catch {
      return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    host.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.className = "scroll-scene__webgl";

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x102b22, 0.014);

    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 180);
    const ambient = new THREE.HemisphereLight(0xe0f0d5, 0x263c32, 2.15);
    scene.add(ambient);
    const sun = new THREE.DirectionalLight(0xf5cf8e, 3.25);
    sun.position.set(-12, 24, 14);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xb1d3b7, 1.05);
    fill.position.set(18, 10, -12);
    scene.add(fill);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(39, 27),
      new THREE.MeshStandardMaterial({ color: 0x244536, roughness: 0.94 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.36;
    scene.add(ground);

    const roadMaterial = new THREE.MeshStandardMaterial({
      color: 0x303a34,
      roughness: 0.9,
      metalness: 0.02,
    });
    const roadGroup = new THREE.Group();
    const addRoad = (width: number, depth: number, x: number, z: number) => {
      const road = new THREE.Mesh(new THREE.BoxGeometry(width, 0.12, depth), roadMaterial);
      road.position.set(x, -0.17, z);
      roadGroup.add(road);
    };
    addRoad(37, 1.55, 0, -10);
    addRoad(37, 1.55, 0, -2);
    addRoad(37, 1.55, 0, 6);
    addRoad(1.8, 25, 0, -1.7);
    scene.add(roadGroup);

    const dashMaterial = new THREE.MeshBasicMaterial({ color: 0xd6c9a2, transparent: true, opacity: 0.72 });
    const addDashes = (axis: "x" | "z", fixed: number) => {
      for (let step = -16; step <= 16; step += 3.2) {
        const dash = new THREE.Mesh(
          new THREE.BoxGeometry(axis === "x" ? 1.35 : 0.06, 0.018, axis === "x" ? 0.06 : 1.05),
          dashMaterial,
        );
        dash.position.set(axis === "x" ? step : fixed, -0.095, axis === "x" ? fixed : step);
        scene.add(dash);
      }
    };
    addDashes("x", -10);
    addDashes("x", 6);
    addDashes("z", 0);

    const park = new THREE.Mesh(
      new THREE.CircleGeometry(3.15, 48),
      new THREE.MeshStandardMaterial({ color: 0x54794e, roughness: 0.9 }),
    );
    park.rotation.x = -Math.PI / 2;
    park.position.set(0, -0.07, 2.05);
    scene.add(park);

    const lotMaterialAvailable = new THREE.MeshStandardMaterial({
      color: 0xc3ab79,
      roughness: 0.81,
      metalness: 0.02,
    });
    const lotMaterialSold = new THREE.MeshStandardMaterial({
      color: 0xa9754e,
      roughness: 0.84,
      metalness: 0.01,
    });
    const lotLineMaterial = new THREE.MeshBasicMaterial({ color: 0xe2d8bd, transparent: true, opacity: 0.62 });
    const xPositions = [-14, -10, -6, 6, 10, 14];
    const zPositions = [-8, -4, 0, 4];
    let lotNumber = 0;
    for (const z of zPositions) {
      for (const x of xPositions) {
        lotNumber += 1;
        const isSold = [2, 4, 7, 8, 11, 13, 16, 18, 20, 23].includes(lotNumber);
        const lot = new THREE.Mesh(
          new THREE.BoxGeometry(3.22, 0.28, 3.08),
          isSold ? lotMaterialSold : lotMaterialAvailable,
        );
        lot.position.set(x, -0.02, z);
        scene.add(lot);

        const plotEdge = new THREE.LineSegments(
          new THREE.EdgesGeometry(new THREE.BoxGeometry(3.22, 0.3, 3.08)),
          new THREE.LineBasicMaterial({ color: 0xf0e6ce, transparent: true, opacity: 0.54 }),
        );
        plotEdge.position.copy(lot.position);
        scene.add(plotEdge);

        if (!isSold && (lotNumber % 4 === 0 || lotNumber === 1)) {
          const trunk = new THREE.Mesh(
            new THREE.CylinderGeometry(0.075, 0.11, 0.58, 7),
            new THREE.MeshStandardMaterial({ color: 0x60472e, roughness: 1 }),
          );
          trunk.position.set(x + 0.9, 0.38, z - 0.84);
          scene.add(trunk);
          const canopy = new THREE.Mesh(
            new THREE.IcosahedronGeometry(0.48, 1),
            new THREE.MeshStandardMaterial({ color: 0x7c9c68, roughness: 0.85 }),
          );
          canopy.position.set(x + 0.9, 0.82, z - 0.84);
          scene.add(canopy);
        }
      }
    }

    // A restrained dotted line leads the camera through the plan like a site-walk route.
    const routePoints = [
      new THREE.Vector3(-15, 0.01, -8),
      new THREE.Vector3(-8, 0.01, -8),
      new THREE.Vector3(-4, 0.01, -2),
      new THREE.Vector3(4, 0.01, -2),
      new THREE.Vector3(9, 0.01, 4),
      new THREE.Vector3(15, 0.01, 4),
    ];
    const route = new THREE.CatmullRomCurve3(routePoints);
    const routeGeometry = new THREE.BufferGeometry().setFromPoints(route.getPoints(90));
    const routeLine = new THREE.Line(
      routeGeometry,
      new THREE.LineDashedMaterial({ color: 0xe3b766, dashSize: 0.42, gapSize: 0.28, transparent: true, opacity: 0.72 }),
    );
    routeLine.computeLineDistances();
    scene.add(routeLine);

    const cameraPath = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-25, 18, 24),
      new THREE.Vector3(-17, 13, 15),
      new THREE.Vector3(-4, 10.5, 12),
      new THREE.Vector3(11, 12, 12),
      new THREE.Vector3(25, 17, 22),
    ]);
    const aimPath = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, 0, 1),
      new THREE.Vector3(2, 0, 0),
      new THREE.Vector3(0, 0, -1),
    ]);

    let targetProgress = 0;
    let easedProgress = 0;
    let previousChapter = -1;
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const readScroll = () => {
      const rect = section.getBoundingClientRect();
      const travel = Math.max(section.offsetHeight - window.innerHeight, 1);
      targetProgress = Math.max(0, Math.min(1, -rect.top / travel));
    };
    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    readScroll();
    resize();
    window.addEventListener("scroll", readScroll, { passive: true });
    window.addEventListener("resize", resize);

    let frame = 0;
    const renderFrame = () => {
      easedProgress = prefersReducedMotion
        ? 0.22
        : easedProgress + (targetProgress - easedProgress) * 0.075;
      camera.position.copy(cameraPath.getPoint(easedProgress));
      camera.lookAt(aimPath.getPoint(easedProgress));

      const nextChapter = Math.min(2, Math.floor(easedProgress * 3));
      if (nextChapter !== previousChapter) {
        previousChapter = nextChapter;
        setActiveChapter(nextChapter);
      }

      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(renderFrame);
    };
    frame = window.requestAnimationFrame(renderFrame);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", readScroll);
      window.removeEventListener("resize", resize);
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.LineSegments) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) material.forEach((item) => item.dispose());
          else material.dispose();
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  const chapter = chapters[activeChapter];

  return (
    <section className="scroll-scene" id="walkthrough" ref={sectionRef} aria-labelledby="walkthrough-title">
      <div className="scroll-scene__pin">
        <div className="scroll-scene__canvas-host" ref={canvasHostRef} />
        <div className="scroll-scene__wash" aria-hidden="true" />
        <div className="scroll-scene__topline">
          <span className="scene-kicker"><span className="scene-kicker__dot" /> THE LAYOUT, IN PERSPECTIVE</span>
          <span className="scene-counter">SCROLL TO MOVE <span>↘</span></span>
        </div>
        <div className="scroll-scene__heading">
          <p className="eyebrow eyebrow--light">A small virtual walk-through</p>
          <h2 id="walkthrough-title">See a little more<br />than the boundary.</h2>
        </div>
        <div className="scroll-scene__story" aria-live="polite" aria-atomic="true">
          <p className="scroll-scene__chapter">0{activeChapter + 1} <span>/</span> {chapter.label}</p>
          <h3>{chapter.title}</h3>
          <p>{chapter.copy}</p>
          <div className="scroll-scene__progress" aria-label={`Step ${activeChapter + 1} of 3`}>
            {chapters.map((item, index) => (
              <span key={item.label} className={index <= activeChapter ? "is-active" : ""} />
            ))}
          </div>
        </div>
        <p className="scroll-scene__disclaimer">Illustrative 3D concept only · not a surveyed or sanctioned layout plan</p>
      </div>
    </section>
  );
}
