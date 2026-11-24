import * as THREE from "three";

let cachedSoftwareGl: boolean | null = null;

/**
 * Detect software WebGL (SwiftShader, llvmpipe, WARP) from the GPU renderer string.
 * Used strictly for rendering cost pacing (never changes scene geometry or layout).
 */
export function detectSoftwareWebGL(): boolean {
  if (cachedSoftwareGl !== null) return cachedSoftwareGl;
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    if (!gl) {
      cachedSoftwareGl = true;
      return true;
    }
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const param = ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER;
    const gpu = String(gl.getParameter(param) || "");
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    cachedSoftwareGl = /SwiftShader|llvmpipe|softpipe|Software|Basic Render/i.test(gpu);
    return cachedSoftwareGl;
  } catch {
    cachedSoftwareGl = true;
    return true;
  }
}

/**
 * Software-WebGL-safe frame pacing using WebGL2 fenceSync so CPU draws never starve the browser.
 */
export function scheduleAfterDraw(
  renderer: THREE.WebGLRenderer,
  cb: () => void,
  setTimerId: (id: number) => void,
) {
  const gl = renderer.getContext();
  if (typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext) {
    const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    const poll = () => {
      const state = sync ? gl.getSyncParameter(sync, gl.SYNC_STATUS) : gl.SIGNALED;
      if (state === gl.UNSIGNALED) {
        setTimerId(window.setTimeout(poll, 8));
      } else {
        if (sync) gl.deleteSync(sync);
        setTimerId(window.setTimeout(cb, 80));
      }
    };
    setTimerId(window.setTimeout(poll, 8));
  } else {
    setTimerId(window.setTimeout(cb, 90));
  }
}

/**
 * Procedural ground turf / mineral stone texture (zero external URLs).
 */
export function createGroundTexture(
  renderer: THREE.WebGLRenderer,
  baseHex: string,
  grainHex: string,
  repeat = 12,
): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = baseHex;
    ctx.fillRect(0, 0, size, size);
    // Subtle architectural grid + mineral speckle
    ctx.strokeStyle = "rgba(212, 175, 95, 0.05)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= size; i += 32) {
      ctx.beginPath();
      ctx.moveTo(i, 0);
      ctx.lineTo(i, size);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i);
      ctx.lineTo(size, i);
      ctx.stroke();
    }
    ctx.fillStyle = grainHex;
    for (let i = 0; i < 650; i++) {
      const x = (i * 73) % size;
      const y = (i * 137) % size;
      const r = (i % 3) + 1;
      ctx.globalAlpha = 0.08 + ((i % 5) * 0.02);
      ctx.fillRect(x, y, r, r);
    }
    ctx.globalAlpha = 1;
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  tex.needsUpdate = true;
  return tex;
}

/**
 * Procedural asphalt road texture with crisp center lane markings.
 */
export function createAsphaltTexture(renderer: THREE.WebGLRenderer): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "#222926";
    ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 800; i++) {
      ctx.fillStyle = i % 2 === 0 ? "rgba(255,255,255,0.035)" : "rgba(0,0,0,0.09)";
      ctx.fillRect((i * 53) % 256, (i * 97) % 256, 2, 2);
    }
    // Subtle gold-sand side kerbs
    ctx.fillStyle = "rgba(212, 175, 95, 0.35)";
    ctx.fillRect(0, 6, 256, 3);
    ctx.fillRect(0, 247, 256, 3);
    // Dashed median
    ctx.fillStyle = "rgba(236, 224, 185, 0.52)";
    for (let x = 10; x < 256; x += 48) {
      ctx.fillRect(x, 126, 24, 4);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(4, 1);
  tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  tex.needsUpdate = true;
  return tex;
}

/**
 * Soft radial contact shadow texture for grounding structures and pedestals.
 */
export function createContactShadowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const grad = ctx.createRadialGradient(64, 64, 8, 64, 64, 62);
    grad.addColorStop(0, "rgba(0, 0, 0, 0.55)");
    grad.addColorStop(0.55, "rgba(0, 0, 0, 0.22)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 128, 128);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  return tex;
}

/**
 * Crisp high-contrast billboard label texture with dynamic width measurement
 * so long labels ("SOLD OUT · ALLOTTED", "18.00 m Wide D.P. Road") never clip or stretch.
 */
export function createLabelSprite(
  renderer: THREE.WebGLRenderer,
  text: string,
  scaleX: number,
  scaleY: number,
  options?: {
    accent?: boolean;
    rotate?: boolean;
    bg?: string;
    borderColor?: string;
    textColor?: string;
    alwaysOnTop?: boolean;
    renderOrder?: number;
    fontSize?: number;
  },
): THREE.Sprite {
  const rotate = options?.rotate ?? false;
  const fontSize = options?.fontSize ?? (options?.accent ? 48 : 50);
  const fontSpec = `700 ${fontSize}px "Plus Jakarta Sans", Arial, sans-serif`;

  // Measure true pixel width so long labels never clip
  const measureCanvas = document.createElement("canvas");
  const mCtx = measureCanvas.getContext("2d");
  let measuredTextW = text.length * fontSize * 0.62;
  if (mCtx) {
    mCtx.font = fontSpec;
    measuredTextW = mCtx.measureText(text).width;
  }

  const padX = options?.bg ? 56 : 36;
  const logicalW = Math.max(220, Math.ceil(measuredTextW + padX * 2));
  const logicalH = options?.bg ? 128 : 112;

  const canvas = document.createElement("canvas");
  canvas.width = rotate ? logicalH : logicalW;
  canvas.height = rotate ? logicalW : logicalH;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (rotate) {
      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.translate(-logicalW / 2, -logicalH / 2);
    }

    if (options?.bg) {
      ctx.fillStyle = options.bg;
      ctx.beginPath();
      ctx.roundRect(10, 12, logicalW - 20, logicalH - 24, 18);
      ctx.fill();
      ctx.strokeStyle =
        options.borderColor ?? (options.accent ? "#F2D27C" : "rgba(255,255,255,0.34)");
      ctx.lineWidth = 5;
      ctx.stroke();
    }

    ctx.font = fontSpec;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = options?.bg ? 6 : 11;
    ctx.strokeStyle = "rgba(4, 10, 7, 0.96)";
    ctx.strokeText(text, logicalW / 2, logicalH / 2 + 2);
    ctx.fillStyle = options?.textColor ?? (options?.accent ? "#F7DF94" : "#FAF8F2");
    ctx.fillText(text, logicalW / 2, logicalH / 2 + 2);
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.generateMipmaps = false;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  tex.needsUpdate = true;

  const mat = new THREE.SpriteMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    depthTest: !(options?.alwaysOnTop ?? false),
    toneMapped: false,
  });
  const sprite = new THREE.Sprite(mat);
  if (options?.renderOrder !== undefined) {
    sprite.renderOrder = options.renderOrder;
  }

  // Preserve exact canvas aspect ratio so text never distorts horizontally or vertically
  const aspect = canvas.width / Math.max(canvas.height, 1);
  if (rotate) {
    sprite.scale.set(scaleX, scaleX / Math.max(aspect, 0.05), 1);
  } else {
    const computedW = scaleY * aspect;
    const finalW = Math.max(scaleX, computedW);
    sprite.scale.set(finalW, finalW / aspect, 1);
  }
  return sprite;
}

/**
 * Crisp road-surface architectural measurement plaque that lies flat above the asphalt
 * and aligns strictly along the road axis (horizontal, vertical, or angled) so full
 * measurements ("9.00 M WIDE ROAD", "12.00 M WIDE ROAD", "18.00 M WIDE D.P. ROAD")
 * are never buried underground, clipped, or overlapping neighboring plots.
 */
export function createRoadLabelMesh(
  renderer: THREE.WebGLRenderer,
  text: string,
  roadW: number,
  roadD: number,
  orient: "h" | "v",
  options?: {
    rotDeg?: number;
    isWideArterial?: boolean;
  },
): THREE.Mesh {
  const isWide = options?.isWideArterial ?? false;
  const fontSize = 68;
  const fontSpec = `800 ${fontSize}px "Plus Jakarta Sans", Arial, sans-serif`;

  const measureCanvas = document.createElement("canvas");
  const mCtx = measureCanvas.getContext("2d");
  let measuredW = text.length * fontSize * 0.64;
  if (mCtx) {
    mCtx.font = fontSpec;
    measuredW = mCtx.measureText(text).width;
  }

  const padX = 72;
  const canvasW = Math.max(420, Math.ceil(measuredW + padX * 2));
  const canvasH = 152;

  const canvas = document.createElement("canvas");
  canvas.width = canvasW;
  canvas.height = canvasH;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, canvasW, canvasH);

    // High-contrast obsidian plaque with crisp brushed-gold rim
    ctx.fillStyle = isWide ? "rgba(7, 17, 12, 0.96)" : "rgba(8, 17, 13, 0.94)";
    ctx.beginPath();
    ctx.roundRect(10, 10, canvasW - 20, canvasH - 20, 26);
    ctx.fill();

    ctx.strokeStyle = isWide ? "#F7DA84" : "#D4AF5F";
    ctx.lineWidth = isWide ? 7 : 5.5;
    ctx.stroke();

    ctx.font = fontSpec;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = 7;
    ctx.strokeStyle = "rgba(3, 8, 6, 0.95)";
    ctx.strokeText(text, canvasW / 2, canvasH / 2 + 2);
    ctx.fillStyle = isWide ? "#FFF5D6" : "#F8F3E6";
    ctx.fillText(text, canvasW / 2, canvasH / 2 + 2);
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = Math.min(16, renderer.capabilities.getMaxAnisotropy());
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;

  const aspect = canvasW / canvasH;
  const alongSpan = orient === "h" ? roadW : roadD;
  const crossSpan = orient === "h" ? roadD : roadW;

  // Size generously for readability while strictly respecting both road width and road length
  const maxPlaneHeight = Math.min(crossSpan * 0.86, isWide ? 10.2 : 7.4);
  let planeHeight = maxPlaneHeight;
  let planeWidth = planeHeight * aspect;

  const maxPlaneWidth = Math.max(alongSpan * 0.84, 18);
  if (planeWidth > maxPlaneWidth) {
    planeWidth = maxPlaneWidth;
    planeHeight = planeWidth / aspect;
  }

  const geo = new THREE.PlaneGeometry(planeWidth, planeHeight);
  geo.rotateX(-Math.PI / 2);

  const mat = new THREE.MeshBasicMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
    toneMapped: false,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 14;

  // Align strictly with road orientation (+ any blueprint angle rotDeg)
  const baseRot = orient === "v" ? Math.PI / 2 : 0;
  const extraRot = options?.rotDeg ? -THREE.MathUtils.degToRad(options.rotDeg) : 0;
  mesh.rotation.y = baseRot + extraRot;

  return mesh;
}

/**
 * Composite 5-part procedural botanical tree (flared root, tapered trunk, 3 asymmetric canopy tiers).
 * Pivoted at bottom-center (y = 0) flush with the ground.
 */
export function buildCompositeTree(scale = 1, tintShift = 0): THREE.Group {
  const group = new THREE.Group();

  const barkMat = new THREE.MeshStandardMaterial({
    color: 0x4b3828,
    roughness: 0.92,
    metalness: 0.02,
  });
  const baseGreen = new THREE.Color(0x2d543c).offsetHSL(tintShift * 0.02, 0.04, tintShift * 0.04);
  const crownMat = new THREE.MeshStandardMaterial({
    color: baseGreen,
    roughness: 0.62,
    metalness: 0.04,
  });
  const accentCrownMat = new THREE.MeshStandardMaterial({
    color: baseGreen.clone().offsetHSL(0.015, 0.05, 0.08),
    roughness: 0.54,
    metalness: 0.05,
  });

  // 1. Flared root collar
  const root = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.36, 0.35, 7), barkMat);
  root.position.y = 0.175;
  group.add(root);

  // 2. Main tapered trunk
  const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.2, 1.85, 7), barkMat);
  trunk.position.y = 1.15;
  group.add(trunk);

  // 3-5. Three layered foliage clusters
  const lowerCanopy = new THREE.Mesh(new THREE.DodecahedronGeometry(0.95, 1), crownMat);
  lowerCanopy.scale.set(1.15, 0.72, 1.1);
  lowerCanopy.position.set(0.12, 2.25, 0.06);
  group.add(lowerCanopy);

  const midCanopy = new THREE.Mesh(new THREE.DodecahedronGeometry(0.78, 1), accentCrownMat);
  midCanopy.scale.set(1.05, 0.85, 0.95);
  midCanopy.position.set(-0.24, 2.75, -0.14);
  group.add(midCanopy);

  const topCrown = new THREE.Mesh(new THREE.DodecahedronGeometry(0.54, 1), accentCrownMat);
  topCrown.position.set(0.06, 3.25, 0.04);
  group.add(topCrown);

  group.scale.setScalar(scale);
  return group;
}

/**
 * Composite 7-part luxury basalt & brushed-brass estate entrance gateway.
 * Pivoted at bottom-center (y = 0).
 */
export function buildEstateGateway(scale = 1): THREE.Group {
  const group = new THREE.Group();

  const stoneMat = new THREE.MeshStandardMaterial({
    color: 0x262e2a,
    roughness: 0.82,
    metalness: 0.08,
  });
  const warmSandMat = new THREE.MeshStandardMaterial({
    color: 0x6f6554,
    roughness: 0.72,
    metalness: 0.06,
  });
  const brassMat = new THREE.MeshStandardMaterial({
    color: 0xd4af5f,
    roughness: 0.24,
    metalness: 0.88,
  });
  const glowMat = new THREE.MeshStandardMaterial({
    color: 0xffe4a8,
    emissive: 0xffc864,
    emissiveIntensity: 1.4,
    roughness: 0.2,
  });

  // Left & Right Stone Plinths
  [-4.2, 4.2].forEach((x) => {
    const plinth = new THREE.Mesh(new THREE.BoxGeometry(2.1, 0.55, 2.1), warmSandMat);
    plinth.position.set(x, 0.275, 0);
    group.add(plinth);

    const pillar = new THREE.Mesh(new THREE.BoxGeometry(1.5, 4.6, 1.5), stoneMat);
    pillar.position.set(x, 2.85, 0);
    group.add(pillar);

    const brassBand = new THREE.Mesh(new THREE.BoxGeometry(1.64, 0.22, 1.64), brassMat);
    brassBand.position.set(x, 4.4, 0);
    group.add(brassBand);
  });

  // Sculpted Canopy Lintel
  const lintel = new THREE.Mesh(new THREE.BoxGeometry(11.4, 0.85, 2.2), stoneMat);
  lintel.position.set(0, 5.4, 0);
  group.add(lintel);

  // Brushed Gold Crest Accent
  const crest = new THREE.Mesh(new THREE.BoxGeometry(8.8, 0.2, 2.32), brassMat);
  crest.position.set(0, 5.9, 0);
  group.add(crest);

  // Under-soffit architectural light strip
  const lightBar = new THREE.Mesh(new THREE.BoxGeometry(6.8, 0.08, 0.32), glowMat);
  lightBar.position.set(0, 4.95, 0);
  group.add(lightBar);

  group.scale.setScalar(scale);
  return group;
}

/**
 * Composite 5-part avenue streetlamp with brushed-brass lantern hood and warm LED diffuser.
 */
export function buildStreetLamp(scale = 1): THREE.Group {
  const group = new THREE.Group();

  const ironMat = new THREE.MeshStandardMaterial({
    color: 0x1a201d,
    roughness: 0.48,
    metalness: 0.72,
  });
  const brassMat = new THREE.MeshStandardMaterial({
    color: 0xd4af5f,
    roughness: 0.22,
    metalness: 0.9,
  });
  const bulbMat = new THREE.MeshStandardMaterial({
    color: 0xfff0c8,
    emissive: 0xffca6b,
    emissiveIntensity: 1.7,
    roughness: 0.15,
  });

  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.26, 0.55, 8), ironMat);
  base.position.y = 0.275;
  group.add(base);

  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.1, 3.3, 8), ironMat);
  pole.position.y = 2.15;
  group.add(pole);

  const arm = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.08, 0.08), brassMat);
  arm.position.set(0.4, 3.72, 0);
  group.add(arm);

  const hood = new THREE.Mesh(new THREE.ConeGeometry(0.28, 0.22, 8), brassMat);
  hood.position.set(0.78, 3.64, 0);
  group.add(hood);

  const diffuser = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 10), bulbMat);
  diffuser.position.set(0.78, 3.48, 0);
  group.add(diffuser);

  group.scale.setScalar(scale);
  return group;
}

/**
 * Composite 3-part survey boundary demarcation pillar with brass cap.
 */
export function buildSurveyPillar(scale = 1): THREE.Group {
  const group = new THREE.Group();

  const concreteMat = new THREE.MeshStandardMaterial({
    color: 0xd8d3c5,
    roughness: 0.88,
    metalness: 0.04,
  });
  const slateMat = new THREE.MeshStandardMaterial({
    color: 0x2c3631,
    roughness: 0.65,
    metalness: 0.18,
  });
  const brassMat = new THREE.MeshStandardMaterial({
    color: 0xd4af5f,
    roughness: 0.2,
    metalness: 0.92,
  });

  const footing = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.18, 0.55), slateMat);
  footing.position.y = 0.09;
  group.add(footing);

  const shaft = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.72, 0.32), concreteMat);
  shaft.position.y = 0.54;
  group.add(shaft);

  const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.22, 0.12, 8), brassMat);
  cap.position.y = 0.96;
  group.add(cap);

  group.scale.setScalar(scale);
  return group;
}

/**
 * Composite 6-part architectural 3D compass rose oriented with True North along -Z.
 */
export function buildCompassMonument(radius = 3.2): THREE.Group {
  const group = new THREE.Group();

  const brassMat = new THREE.MeshStandardMaterial({
    color: 0xd4af5f,
    roughness: 0.22,
    metalness: 0.92,
  });
  const darkBronzeMat = new THREE.MeshStandardMaterial({
    color: 0x3b443e,
    roughness: 0.55,
    metalness: 0.55,
  });
  const northAccentMat = new THREE.MeshStandardMaterial({
    color: 0xe6b85c,
    emissive: 0x7c5718,
    emissiveIntensity: 0.45,
    roughness: 0.18,
    metalness: 0.9,
  });

  // Outer engraved ring
  const outerRing = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.09, 10, 48), brassMat);
  outerRing.rotation.x = Math.PI / 2;
  outerRing.position.y = 0.14;
  group.add(outerRing);

  // Inner concentric dial ring
  const innerRing = new THREE.Mesh(new THREE.TorusGeometry(radius * 0.78, 0.045, 8, 40), darkBronzeMat);
  innerRing.rotation.x = Math.PI / 2;
  innerRing.position.y = 0.14;
  group.add(innerRing);

  // North spire pointing to -Z
  const northNeedle = new THREE.Mesh(new THREE.ConeGeometry(radius * 0.14, radius * 0.62, 4), northAccentMat);
  northNeedle.rotation.x = -Math.PI / 2;
  northNeedle.position.set(0, 0.22, -radius * 0.85);
  group.add(northNeedle);

  // South, East, West cardinal ticks
  const tickGeo = new THREE.ConeGeometry(radius * 0.08, radius * 0.34, 4);
  const southTick = new THREE.Mesh(tickGeo, darkBronzeMat);
  southTick.rotation.x = Math.PI / 2;
  southTick.position.set(0, 0.2, radius * 0.82);
  group.add(southTick);

  const eastTick = new THREE.Mesh(tickGeo, darkBronzeMat);
  eastTick.rotation.z = -Math.PI / 2;
  eastTick.position.set(radius * 0.82, 0.2, 0);
  group.add(eastTick);

  const westTick = new THREE.Mesh(tickGeo, darkBronzeMat);
  westTick.rotation.z = Math.PI / 2;
  westTick.position.set(-radius * 0.82, 0.2, 0);
  group.add(westTick);

  return group;
}

/**
 * Composite luxury contemporary villa + compound wall + sculpted SOLD OUT estate plaque
 * placed directly atop sold-out land parcels inside the 360° Plot Inspector.
 */
export function buildSoldPropertyModel(
  renderer: THREE.WebGLRenderer,
  parcelWidth: number,
  parcelDepth: number,
): THREE.Group {
  const group = new THREE.Group();

  const bw = parcelWidth * 0.56;
  const bd = parcelDepth * 0.52;
  const compoundW = parcelWidth * 0.86;
  const compoundD = parcelDepth * 0.84;

  const limestoneMat = new THREE.MeshStandardMaterial({
    color: 0xe6dfd0,
    roughness: 0.72,
    metalness: 0.05,
  });
  const basaltMat = new THREE.MeshStandardMaterial({
    color: 0x2a312e,
    roughness: 0.68,
    metalness: 0.14,
  });
  const brassMat = new THREE.MeshStandardMaterial({
    color: 0xd4af5f,
    roughness: 0.24,
    metalness: 0.88,
  });
  const windowGlowMat = new THREE.MeshStandardMaterial({
    color: 0xffe6b3,
    emissive: 0xffbf5e,
    emissiveIntensity: 0.95,
    roughness: 0.2,
    metalness: 0.15,
  });
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x9b9282,
    roughness: 0.86,
    metalness: 0.04,
  });

  // 1. Low perimeter compound boundary walls (front gap for entrance gate)
  const wallH = 0.55;
  const wallT = 0.14;
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(compoundW, wallH, wallT), wallMat);
  backWall.position.set(0, wallH / 2, -compoundD / 2);
  group.add(backWall);

  const leftWall = new THREE.Mesh(new THREE.BoxGeometry(wallT, wallH, compoundD), wallMat);
  leftWall.position.set(-compoundW / 2, wallH / 2, 0);
  group.add(leftWall);

  const rightWall = new THREE.Mesh(new THREE.BoxGeometry(wallT, wallH, compoundD), wallMat);
  rightWall.position.set(compoundW / 2, wallH / 2, 0);
  group.add(rightWall);

  // Front wall split with brass gate piers
  const segW = compoundW * 0.34;
  [-1, 1].forEach((side) => {
    const fWall = new THREE.Mesh(new THREE.BoxGeometry(segW, wallH, wallT), wallMat);
    fWall.position.set(side * (compoundW / 2 - segW / 2), wallH / 2, compoundD / 2);
    group.add(fWall);

    const pier = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.78, 0.28), basaltMat);
    pier.position.set(side * (compoundW / 2 - segW), 0.39, compoundD / 2);
    group.add(pier);

    const cap = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.08, 0.34), brassMat);
    cap.position.set(side * (compoundW / 2 - segW), 0.82, compoundD / 2);
    group.add(cap);
  });

  // 2. Elevated stone villa podium
  const podium = new THREE.Mesh(new THREE.BoxGeometry(bw + 0.55, 0.36, bd + 0.55), basaltMat);
  podium.position.set(0, 0.18, -0.15);
  group.add(podium);

  // 3. Ground floor limestone volume
  const gfH = 1.45;
  const groundFloor = new THREE.Mesh(new THREE.BoxGeometry(bw, gfH, bd), limestoneMat);
  groundFloor.position.set(0, 0.36 + gfH / 2, -0.15);
  groundFloor.castShadow = true;
  groundFloor.receiveShadow = true;
  group.add(groundFloor);

  // Ground floor panoramic glass bands
  const gfGlass = new THREE.Mesh(new THREE.BoxGeometry(bw * 0.68, 0.62, bd + 0.08), windowGlowMat);
  gfGlass.position.set(0, 1.05, -0.15);
  group.add(gfGlass);

  // 4. Cantilevered first-floor architectural terrace slab + upper suite
  const slab = new THREE.Mesh(new THREE.BoxGeometry(bw + 0.42, 0.18, bd + 0.42), basaltMat);
  slab.position.set(0, 0.36 + gfH + 0.09, -0.15);
  group.add(slab);

  const ufW = bw * 0.72;
  const ufD = bd * 0.74;
  const ufH = 1.25;
  const upperFloor = new THREE.Mesh(new THREE.BoxGeometry(ufW, ufH, ufD), limestoneMat);
  upperFloor.position.set(-bw * 0.08, 0.36 + gfH + 0.18 + ufH / 2, -0.25);
  upperFloor.castShadow = true;
  group.add(upperFloor);

  const ufGlass = new THREE.Mesh(new THREE.BoxGeometry(ufW * 0.64, 0.54, ufD + 0.08), windowGlowMat);
  ufGlass.position.set(-bw * 0.08, 0.36 + gfH + 0.85, -0.25);
  group.add(ufGlass);

  // 5. Roof canopy + brushed brass fascia trim
  const roof = new THREE.Mesh(new THREE.BoxGeometry(ufW + 0.5, 0.18, ufD + 0.5), basaltMat);
  roof.position.set(-bw * 0.08, 0.36 + gfH + 0.18 + ufH + 0.09, -0.25);
  group.add(roof);

  const fascia = new THREE.Mesh(new THREE.BoxGeometry(ufW + 0.56, 0.07, ufD + 0.56), brassMat);
  fascia.position.set(-bw * 0.08, 0.36 + gfH + 0.18 + ufH + 0.21, -0.25);
  group.add(fascia);

  // 6. High-contrast floating architectural "SOLD OUT · ALLOTTED" plaque above the residence
  const soldPlaque = createLabelSprite(renderer, "SOLD OUT · ALLOTTED", 7.4, 1.55, {
    bg: "rgba(148, 28, 22, 0.96)",
    borderColor: "#F7DF94",
    textColor: "#FFFFFF",
    accent: true,
    alwaysOnTop: true,
    renderOrder: 30,
    fontSize: 52,
  });
  soldPlaque.position.set(0, 0.36 + gfH + ufH + 2.2, 0);
  group.add(soldPlaque);

  return group;
}

