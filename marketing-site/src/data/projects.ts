export type PlotStatus = "available" | "reserved" | "sold";
export type Facing = "N" | "S" | "E" | "W";

export type PlotCell = {
  no: string; // Unique key/ID used for URLs, selection, and CRM (e.g. "12" or "K-05")
  displayNo?: string; // Exact sheet number shown on the layout plan (e.g. "5")
  wing?: string; // Optional wing name (e.g. "Ananta Wing", "Akasha Wing")
  x: number;
  z: number;
  w: number; // width in meters
  d: number; // depth in meters
  rotDeg?: number; // optional rotation in degrees (clockwise in top-down XZ)
  areaSqFt: number;
  areaSqM: number;
  faces: Facing;
  road: string;
  status: PlotStatus;
  corner?: boolean;
  parkFacing?: boolean;
  ratePerSqFt: number;
  category?: "Residential" | "Commercial";
  plcPercent: number;
  vastuNote: string;
};

export type RoadSegment = {
  x: number;
  z: number;
  w: number;
  d: number;
  rotDeg?: number;
  label: string;
  orient: "h" | "v";
};

export type LayoutZone = {
  x: number;
  z: number;
  w: number;
  d: number;
  rotDeg?: number;
  label: string;
  kind: "open" | "amenity" | "nala" | "vacant";
};

export type ProjectLayout = {
  bounds: { x: number; z: number; w: number; d: number };
  roads: RoadSegment[];
  zones: LayoutZone[];
  plots: PlotCell[];
};

export type ProjectCompliance = {
  authority: string;
  sanctionRef: string;
  digitalSignStamp: string;
  phNo: string;
  regulationRef: string;
  proRataFsi: string;
  openAndAmenity: string;
  reraStatus: string;
};

export type EstateProject = {
  slug: string;
  name: string;
  corridor: string;
  location: string;
  district: string;
  state: string;
  khNo: string;
  mouza: string;
  heroImage: string;
  tagline: string;
  headline: string;
  headlineEm: string;
  intro: string;
  emotionalPitch: string;
  frontage: string;
  baseRatePerSqFt: number;
  commercialRatePerSqFt?: number;
  idealFor: string[];
  compliance: ProjectCompliance;
  facts: Array<{ label: string; value: string; note: string }>;
  claims: string[];
  landmarks: Array<{ name: string; note: string }>;
  faqs: Array<{ question: string; answer: string }>;
  layout: ProjectLayout;
};

export const STATUS_META: Record<
  PlotStatus,
  { label: string; color: string; hex: number; badgeText: string }
> = {
  available: {
    label: "Available",
    color: "#6FA07A",
    hex: 0x6fa07a,
    badgeText: "Open on layout sheet",
  },
  reserved: {
    label: "Reserved",
    color: "#D4AF5F",
    hex: 0xd4af5f,
    badgeText: "Marked reserved on sheet",
  },
  sold: {
    label: "Sold Out",
    color: "#A95B45",
    hex: 0xa95b45,
    badgeText: "Marked sold on sanctioned layout",
  },
};

export const FACING_LABEL: Record<Facing, string> = {
  N: "North-Facing",
  S: "South-Facing",
  E: "East-Facing",
  W: "West-Facing",
};

export const FACING_AZIMUTH: Record<Facing, number> = {
  N: 0,
  E: 90,
  S: 180,
  W: 270,
};

export const BUYER_PERSONAS = [
  "Legacy Investor",
  "Doctor / Medical Professional",
  "Small Business Owner",
  "Parent Securing Children's Future",
  "Government / PSU Official",
  "High-Value Land Acquirer",
] as const;

const VASTU_NOTES: Record<Facing, string> = {
  E: "Morning sunrise frontage favored for family residences & medical chambers.",
  N: "Kubera north frontage prized for balanced light and long-term appreciation.",
  W: "Evening-lit frontage suited for shaded verandas and corner elevation design.",
  S: "Generous frontage width allowing architectural courtyard and solar setbacks.",
};

function makePlot(opts: {
  no: string;
  displayNo?: string;
  wing?: string;
  x: number;
  z: number;
  w: number;
  d: number;
  rotDeg?: number;
  areaSqM?: number;
  faces: Facing;
  road: string;
  status: PlotStatus;
  baseRate: number;
  category?: "Residential" | "Commercial";
  corner?: boolean;
  parkFacing?: boolean;
}): PlotCell {
  const areaSqM = Number((opts.areaSqM ?? opts.w * opts.d).toFixed(2));
  const areaSqFt = Math.round(areaSqM * 10.7639);
  const plcPercent = (opts.corner ? 5 : 0) + (opts.parkFacing ? 3 : 0);
  const category =
    opts.category ?? (/12\.00|18\.00|30\.00/.test(opts.road) ? "Commercial" : "Residential");
  return {
    no: opts.no,
    displayNo: opts.displayNo ?? opts.no,
    wing: opts.wing,
    x: Number(opts.x.toFixed(2)),
    z: Number(opts.z.toFixed(2)),
    w: Number(opts.w.toFixed(2)),
    d: Number(opts.d.toFixed(2)),
    rotDeg: opts.rotDeg ?? 0,
    areaSqM,
    areaSqFt,
    faces: opts.faces,
    road: opts.road,
    status: opts.status,
    corner: opts.corner ?? false,
    parkFacing: opts.parkFacing ?? false,
    ratePerSqFt: opts.baseRate,
    category,
    plcPercent,
    vastuNote: VASTU_NOTES[opts.faces],
  };
}

// ============================================================================
// 1. SAMRUDDHI ESTATE PHASE 3 — SALAI DABHA KH. NO. 110 (76 Exact Plots: 01-76)
// Reference: SALAI_DHABHA_110-Model.pdf & Marked Sheet "Salai - 110"
// Uncolored (Available) on sheet: Plots 12 & 34. All other 74 plots yellow-marked SOLD.
// ============================================================================
function buildSamruddhiPhase3Layout(): ProjectLayout {
  const base = 1700; // Residential Start @ Rs. 1,700/-
  const comm = 1750; // Commercial Start @ Rs. 1,750/-
  const availableSet = new Set(["12", "34"]);
  const statusOf = (no: string): PlotStatus => (availableSet.has(no) ? "available" : "sold");
  const plots: PlotCell[] = [];

  // North Strip (North of 12.00 m Wide Road, z around -64 to -40)
  // Block NW (West of Road 1): 71, 72
  plots.push(
    makePlot({ no: "71", x: -73, z: -61, w: 14.5, d: 10.0, areaSqM: 155.8, faces: "E", road: "9.00 m Wide Road", status: statusOf("71"), baseRate: base, corner: true }),
    makePlot({ no: "72", x: -73, z: -49, w: 14.0, d: 14.0, areaSqM: 193.5, faces: "S", road: "12.00 m Wide Road", status: statusOf("72"), baseRate: comm, category: "Commercial", corner: true }),
  );

  // Block N2 (between Road 1 & Road 2): West 70, 69 | East 67, 68
  plots.push(
    makePlot({ no: "70", x: -45, z: -61, w: 12.5, d: 10.3, areaSqM: 128.4, faces: "W", road: "9.00 m Wide Road", status: statusOf("70"), baseRate: base }),
    makePlot({ no: "69", x: -45, z: -49, w: 12.5, d: 13.5, areaSqM: 161.0, faces: "S", road: "12.00 m Wide Road", status: statusOf("69"), baseRate: comm, category: "Commercial", corner: true }),
    makePlot({ no: "67", x: -32, z: -61, w: 12.5, d: 10.1, areaSqM: 126.8, faces: "E", road: "9.00 m Wide Road", status: statusOf("67"), baseRate: base }),
    makePlot({ no: "68", x: -32, z: -49, w: 12.5, d: 13.5, areaSqM: 161.0, faces: "S", road: "12.00 m Wide Road", status: statusOf("68"), baseRate: comm, category: "Commercial", corner: true }),
  );

  // Block N3 (between Road 2 & Road 3): West 66, 65 | East 45, 46
  plots.push(
    makePlot({ no: "66", x: -3, z: -61, w: 14.0, d: 10.35, areaSqM: 144.9, faces: "W", road: "9.00 m Wide Road", status: statusOf("66"), baseRate: base }),
    makePlot({ no: "65", x: -3, z: -49, w: 14.0, d: 13.0, areaSqM: 174.3, faces: "S", road: "12.00 m Wide Road", status: statusOf("65"), baseRate: comm, category: "Commercial", corner: true }),
    makePlot({ no: "45", x: 11.5, z: -61, w: 14.0, d: 10.15, areaSqM: 142.1, faces: "E", road: "9.00 m Wide Road", status: statusOf("45"), baseRate: base }),
    makePlot({ no: "46", x: 11.5, z: -49, w: 14.0, d: 13.0, areaSqM: 174.3, faces: "S", road: "12.00 m Wide Road", status: statusOf("46"), baseRate: comm, category: "Commercial", corner: true }),
  );

  // Block N4 (between Road 3 & Road 4): West 44, 43 | East 23, 24
  plots.push(
    makePlot({ no: "44", x: 41, z: -61, w: 12.5, d: 10.2, areaSqM: 127.5, faces: "W", road: "9.00 m Wide Road", status: statusOf("44"), baseRate: base }),
    makePlot({ no: "43", x: 41, z: -49, w: 12.5, d: 12.65, areaSqM: 150.4, faces: "S", road: "12.00 m Wide Road", status: statusOf("43"), baseRate: comm, category: "Commercial", corner: true }),
    makePlot({ no: "23", x: 54, z: -61, w: 12.5, d: 10.0, areaSqM: 125.0, faces: "E", road: "9.00 m Wide Road", status: statusOf("23"), baseRate: base }),
    makePlot({ no: "24", x: 54, z: -49, w: 12.5, d: 12.65, areaSqM: 150.4, faces: "S", road: "12.00 m Wide Road", status: statusOf("24"), baseRate: comm, category: "Commercial", corner: true }),
  );

  // Block NE (between Road 4 & Road 5): West 22, 21 | East 01, 02
  plots.push(
    makePlot({ no: "22", x: 84, z: -61, w: 15.0, d: 9.85, areaSqM: 147.8, faces: "W", road: "9.00 m Wide Road", status: statusOf("22"), baseRate: base }),
    makePlot({ no: "21", x: 84, z: -49, w: 15.0, d: 12.6, areaSqM: 181.3, faces: "S", road: "12.00 m Wide Road", status: statusOf("21"), baseRate: comm, category: "Commercial", corner: true }),
    makePlot({ no: "01", x: 99.5, z: -61, w: 14.8, d: 9.45, areaSqM: 148.0, faces: "E", road: "9.00 m Wide Road", status: statusOf("01"), baseRate: base, corner: true }),
    makePlot({ no: "02", x: 99.5, z: -49, w: 14.9, d: 12.6, areaSqM: 181.4, faces: "S", road: "12.00 m Wide Road", status: statusOf("02"), baseRate: comm, category: "Commercial", corner: true }),
  );

  // South of 12.00 m Wide Road
  // Strip flanking Amenity Space (73, 74, 75, 76 — 21.20m wide facing East onto 9m Road)
  [
    { no: "73", z: -20, d: 11.1, area: 227.6, corner: true },
    { no: "74", z: -9.5, d: 9.5, area: 201.4, corner: false },
    { no: "75", z: 0.5, d: 9.5, area: 201.4, corner: false },
    { no: "76", z: 10.5, d: 9.5, area: 201.4, corner: false },
  ].forEach((row) => {
    plots.push(
      makePlot({
        no: row.no,
        x: -44,
        z: row.z,
        w: 21.2,
        d: row.d,
        areaSqM: row.area,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(row.no),
        baseRate: base,
        corner: row.corner,
        parkFacing: true,
      }),
    );
  });

  // Block S2 (West col: 64 down to 56 | East col: 47 down to 55) — 14.00m wide each
  const s2West = ["64", "63", "62", "61", "60", "59", "58", "57", "56"];
  const s2East = ["47", "48", "49", "50", "51", "52", "53", "54", "55"];
  const s2Depths = [13.5, 8.5, 8.5, 8.5, 8.5, 8.5, 8.5, 8.5, 10.6];
  let curZ = -26;
  s2West.forEach((no, idx) => {
    const d = s2Depths[idx];
    const zCenter = curZ + d / 2;
    const eNo = s2East[idx];
    plots.push(
      makePlot({
        no,
        x: -10,
        z: zCenter,
        w: 14.0,
        d,
        areaSqM: idx === 0 ? 181.3 : idx === 8 ? 148.4 : 119.0,
        faces: "W",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: idx === 0 || idx === 8,
        parkFacing: idx <= 4,
      }),
      makePlot({
        no: eNo,
        x: 4.5,
        z: zCenter,
        w: 14.0,
        d,
        areaSqM: idx === 0 ? 181.3 : idx === 8 ? 136.5 : 119.0,
        faces: "E",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(eNo),
        baseRate: base,
        corner: idx === 0 || idx === 8,
      }),
    );
    curZ += d + 0.5;
  });

  // Block S3 (West col: 42 down to 34 [34 AVAILABLE!] | East col: 25 down to 33) — 12.50m wide each
  const s3West = ["42", "41", "40", "39", "38", "37", "36", "35", "34"];
  const s3East = ["25", "26", "27", "28", "29", "30", "31", "32", "33"];
  const s3Depths = [13.5, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 11.85];
  curZ = -26;
  s3West.forEach((no, idx) => {
    const d = s3Depths[idx];
    const zCenter = curZ + d / 2;
    const eNo = s3East[idx];
    plots.push(
      makePlot({
        no,
        x: 34,
        z: zCenter,
        w: 12.5,
        d,
        areaSqM: idx === 0 ? 161.0 : idx === 8 ? 148.1 : 100.0,
        faces: "W",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: idx === 0 || idx === 8,
      }),
      makePlot({
        no: eNo,
        x: 47,
        z: zCenter,
        w: 12.5,
        d,
        areaSqM: idx === 0 ? 161.0 : idx === 8 ? 138.4 : 100.0,
        faces: "E",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(eNo),
        baseRate: base,
        corner: idx === 0 || idx === 8,
      }),
    );
    curZ += d + 0.5;
  });

  // Block S4 (West col: 20 down to 12 [12 AVAILABLE!] | East col: 03 down to 11) — 15.00m wide each
  const s4West = ["20", "19", "18", "17", "16", "15", "14", "13", "12"];
  const s4East = ["03", "04", "05", "06", "07", "08", "09", "10", "11"];
  const s4Depths = [13.25, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 10.0];
  curZ = -26;
  s4West.forEach((no, idx) => {
    const d = s4Depths[idx];
    const zCenter = curZ + d / 2;
    const eNo = s4East[idx];
    plots.push(
      makePlot({
        no,
        x: 78,
        z: zCenter,
        w: 15.0,
        d,
        areaSqM: idx === 0 ? 191.0 : idx === 8 ? 150.0 : 120.0,
        faces: "W",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: idx === 0 ? comm : base,
        corner: idx === 0 || idx === 8,
      }),
      makePlot({
        no: eNo,
        x: 93.5,
        z: zCenter,
        w: 15.1,
        d,
        areaSqM: idx === 0 ? 193.5 : idx === 8 ? 154.0 : 121.2,
        faces: "E",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(eNo),
        baseRate: idx === 0 ? comm : base,
        corner: idx === 0 || idx === 8,
      }),
    );
    curZ += d + 0.5;
  });

  return {
    bounds: { x: 16, z: 0, w: 220, d: 152 },
    roads: [
      { x: 16, z: -34, w: 212, d: 12, label: "12.00 m Wide Road", orient: "h" },
      { x: -58, z: -55, w: 9, d: 34, label: "9.00 m Road", orient: "v" },
      { x: -24.5, z: 4, w: 9, d: 138, label: "9.00 m Wide Road", orient: "v" },
      { x: 20, z: 4, w: 9, d: 138, label: "9.00 m Wide Road", orient: "v" },
      { x: 63, z: 4, w: 9, d: 138, label: "9.00 m Wide Road", orient: "v" },
      { x: 109, z: 4, w: 9, d: 138, label: "9.00 m Wide Road", orient: "v" },
    ],
    zones: [
      { x: -72, z: -6, w: 28, d: 38, label: "Amenity Space · 964.26 sq.m", kind: "amenity" },
      { x: -64, z: 38, w: 46, d: 42, label: "Open Space · 1,931.19 sq.m", kind: "open" },
    ],
    plots,
  };
}

// ============================================================================
// 2. SAMRUDDHI ESTATE PHASE 4 — SALAI DABHA KH. NO. 111 (83 Exact Plots: 01-83)
// Reference: SALAIDABHA_111-Model.pdf & Marked Sheet "सालई - 111"
// Yellow-marked SOLD (28): 02-06, 14-19, 30-32, 44-52, 63-65, 72, 74
// Uncolored AVAILABLE (55): remaining plots
// ============================================================================
function buildSamruddhiPhase4Layout(): ProjectLayout {
  const base = 1700; // Residential Start @ Rs. 1,700/-
  const comm = 1750; // Commercial Start @ Rs. 1,750/-
  const soldSet = new Set([
    "02", "03", "04", "05", "06", "14", "15",
    "16", "17", "18", "19", "30", "31", "32",
    "44", "45", "46", "47", "48", "49", "50", "51", "52",
    "63", "64", "65", "72", "74",
  ]);
  const statusOf = (no: string): PlotStatus => (soldSet.has(no) ? "sold" : "available");
  const plots: PlotCell[] = [];

  // NW Plot 01 (adjoining Open Space 1 & 18m DP Road — Commercial Frontage)
  plots.push(
    makePlot({
      no: "01",
      x: -74,
      z: -56,
      w: 22.0,
      d: 18.0,
      areaSqM: 430.0,
      faces: "N",
      road: "18.00 m Wide D.P. Road",
      status: statusOf("01"),
      baseRate: comm,
      category: "Commercial",
      corner: true,
      parkFacing: true,
    }),
  );

  // NE Block North Row (facing 18.00 m Wide D.P. Road — Commercial Frontage): 02, 03, 04, 05, 06
  [
    { no: "02", x: 18, w: 14.5, d: 23.2, area: 337.1 },
    { no: "03", x: 33.5, w: 14.5, d: 22.7, area: 329.2 },
    { no: "04", x: 49, w: 14.5, d: 22.0, area: 318.9 },
    { no: "05", x: 64.5, w: 14.5, d: 21.3, area: 308.6 },
    { no: "06", x: 78, w: 10.5, d: 20.5, area: 215.0 },
  ].forEach((item, idx) => {
    plots.push(
      makePlot({
        no: item.no,
        x: item.x,
        z: -56,
        w: item.w,
        d: item.d,
        areaSqM: item.area,
        faces: "N",
        road: "18.00 m Wide D.P. Road",
        status: statusOf(item.no),
        baseRate: comm,
        category: "Commercial",
        corner: idx === 0 || idx === 4,
      }),
    );
  });

  // NE Block South Row (facing 9.00 m Wide Road): 15, 14, 13, 12, 11, 10, 09, 08, 07
  const neSouth = ["15", "14", "13", "12", "11", "10", "09", "08", "07"];
  let nx = 10.5;
  neSouth.forEach((no, idx) => {
    const w = idx === 0 ? 10.95 : idx === 8 ? 9.3 : 7.35;
    plots.push(
      makePlot({
        no,
        x: nx + w / 2,
        z: -36.5,
        w,
        d: 13.5,
        areaSqM: idx === 0 ? 140.1 : idx === 8 ? 125.6 : 99.2,
        faces: "S",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: idx === 0 || idx === 8,
      }),
    );
    nx += w + 0.55;
  });

  // Far-West Strip (below Open Space 2): 80, 81, 82, 83
  [
    { no: "80", z: 18, w: 15.75, d: 9.0, area: 141.8 },
    { no: "81", z: 27.6, w: 15.6, d: 9.0, area: 140.4 },
    { no: "82", z: 37.2, w: 15.4, d: 9.0, area: 138.6 },
    { no: "83", z: 47.0, w: 15.0, d: 9.85, area: 146.5 },
  ].forEach((row, idx) => {
    plots.push(
      makePlot({
        no: row.no,
        x: -74,
        z: row.z,
        w: row.w,
        d: row.d,
        areaSqM: row.area,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(row.no),
        baseRate: base,
        corner: idx === 3,
        parkFacing: idx === 0,
      }),
    );
  });

  // 4 Double-Column South Blocks (S1, S2, S3, S4) separated by 9.00 m Wide Roads
  const southBlocks: Array<{
    wx: number;
    ex: number;
    w: number;
    westNos: string[];
    eastNos: string[];
    depths: number[];
    baseArea: number;
  }> = [
    {
      wx: -47,
      ex: -34,
      w: 12.5,
      westNos: ["79", "78", "77", "76", "75", "74", "73", "72"],
      eastNos: ["64", "65", "66", "67", "68", "69", "70", "71"],
      depths: [12.55, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 10.2],
      baseArea: 100.0,
    },
    {
      wx: -11,
      ex: 3.5,
      w: 14.0,
      westNos: ["63", "62", "61", "60", "59", "58", "57", "56"],
      eastNos: ["48", "49", "50", "51", "52", "53", "54", "55"],
      depths: [11.75, 8.5, 8.5, 8.5, 8.5, 8.5, 8.5, 9.5],
      baseArea: 119.0,
    },
    {
      wx: 28,
      ex: 41,
      w: 12.5,
      westNos: ["47", "46", "45", "44", "43", "42", "41", "40"],
      eastNos: ["32", "33", "34", "35", "36", "37", "38", "39"],
      depths: [12.3, 8.5, 8.5, 8.5, 8.5, 8.5, 8.5, 10.3],
      baseArea: 106.25,
    },
    {
      wx: 66,
      ex: 81,
      w: 14.3,
      westNos: ["31", "30", "29", "28", "27", "26", "25", "24"],
      eastNos: ["16", "17", "18", "19", "20", "21", "22", "23"],
      depths: [11.55, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.4],
      baseArea: 128.7,
    },
  ];

  southBlocks.forEach((blk) => {
    let cz = -14;
    blk.westNos.forEach((wNo, idx) => {
      const d = blk.depths[idx];
      const zCenter = cz + d / 2;
      const eNo = blk.eastNos[idx];
      const isEnd = idx === 0 || idx === blk.westNos.length - 1;
      const area = isEnd ? Number((blk.w * d - (idx === 0 ? 7.7 : 0)).toFixed(1)) : blk.baseArea;
      plots.push(
        makePlot({
          no: wNo,
          x: blk.wx,
          z: zCenter,
          w: blk.w,
          d,
          areaSqM: area,
          faces: "W",
          road: "9.00 m Wide Road",
          status: statusOf(wNo),
          baseRate: base,
          corner: isEnd,
        }),
        makePlot({
          no: eNo,
          x: blk.ex,
          z: zCenter,
          w: blk.w,
          d,
          areaSqM: area,
          faces: "E",
          road: "9.00 m Wide Road",
          status: statusOf(eNo),
          baseRate: base,
          corner: isEnd,
        }),
      );
      cz += d + 0.55;
    });
  });

  return {
    bounds: { x: 8, z: -2, w: 196, d: 158 },
    roads: [
      { x: 8, z: -74, w: 188, d: 14, label: "18.00 m Wide D.P. Road", orient: "h" },
      { x: 8, z: -23, w: 188, d: 9, label: "9.00 m Wide Road", orient: "h" },
      { x: -60, z: 22, w: 9, d: 90, label: "9.00 m Wide Road", orient: "v" },
      { x: -23, z: 22, w: 9, d: 90, label: "9.00 m Wide Road", orient: "v" },
      { x: 16, z: -2, w: 9, d: 132, label: "9.00 m Wide Road", orient: "v" },
      { x: 53, z: 22, w: 9, d: 90, label: "9.00 m Wide Road", orient: "v" },
      { x: 93, z: 22, w: 9, d: 90, label: "9.00 m Wide Road", orient: "v" },
    ],
    zones: [
      { x: -38, z: -48, w: 36, d: 34, label: "Open Space 1 · 1,471.58 sq.m", kind: "open" },
      { x: -6, z: -48, w: 24, d: 34, label: "Amenity · 1,000.32 sq.m", kind: "amenity" },
      { x: -74, z: -2, w: 16, d: 26, label: "Open Space 2 · 529.70 sq.m", kind: "open" },
    ],
    plots,
  };
}

// ============================================================================
// 3. BHAGIRATH NAGARI — MOUZA KANHOLI SR. NO. 87/1 (253 Exact Plots: 1-253)
// Reference: NMRDA-26-36337 Digitally Signed 3-Page PDF & Marked Color Photo
// Yellow & Orange colored plots on photo = SOLD OUT; uncolored = AVAILABLE
// ============================================================================
function buildBhagirathNagariLayout(): ProjectLayout {
  const base = 1990; // Residential Start @ Rs. 1,990/-
  const comm = 2050; // Commercial Start @ Rs. 2,050/-
  const soldSet = new Set([
    // SE Diagonal Strip (yellow) + corner 36 (orange)
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "36",
    // South Horizontal Block (yellow & orange)
    "16", "17", "23", "24", "26", "27", "28", "29", "30", "31", "32", "35",
    // Block E top (orange)
    "60", "84",
    // Block D (orange)
    "85", "86", "89", "90", "91", "92", "93", "94", "97", "98",
    "102", "105", "106", "107", "110", "113", "114",
    // Block C (orange)
    "115", "116", "117", "119", "121", "125", "127",
    "131", "137", "139", "140", "141", "142", "143", "144", "145", "146",
    // Block B (orange)
    "147", "163", "164",
    // North of 12m Spine (orange 200 + yellow 201, 202, 214, 215)
    "200", "201", "202", "214", "215",
  ]);
  const statusOf = (no: string): PlotStatus => (soldSet.has(no) ? "sold" : "available");
  const plots: PlotCell[] = [];

  // --- A. NORTH OF 12.00 m SPINE (z < -18) ---
  // Far-NW Column: 253 down to 240 (14 plots, 14.1m x 8.6m)
  for (let i = 0; i < 14; i++) {
    const no = String(253 - i);
    const isCorner = i === 0 || i === 13;
    const d = isCorner ? 9.8 : 8.6;
    plots.push(
      makePlot({
        no,
        x: -138,
        z: -158 + i * 9.3,
        w: 14.1,
        d,
        areaSqM: i === 0 ? 157.6 : i === 13 ? 133.3 : 121.26,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isCorner,
      }),
    );
  }

  // NW Double Block 1: West 227..239 (13 plots) | East 226..216 (11 plots)
  plots.push(
    makePlot({ no: "227", x: -112, z: -146, w: 15.0, d: 18.0, areaSqM: 330.26, faces: "W", road: "9.00 m Wide Road", status: statusOf("227"), baseRate: base + 50, corner: true }),
    makePlot({ no: "226", x: -96, z: -142, w: 15.0, d: 20.0, areaSqM: 367.21, faces: "E", road: "9.00 m Wide Road", status: statusOf("226"), baseRate: base + 50, corner: true }),
  );
  for (let i = 0; i < 12; i++) {
    const no = String(228 + i);
    const isEnd = i === 11;
    plots.push(
      makePlot({
        no,
        x: -112,
        z: -129 + i * 8.8,
        w: 15.0,
        d: isEnd ? 10.0 : 8.25,
        areaSqM: isEnd ? 142.27 : 123.75,
        faces: "W",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }
  for (let i = 0; i < 10; i++) {
    const no = String(225 - i);
    const isEnd = i === 9;
    plots.push(
      makePlot({
        no,
        x: -96,
        z: -111 + i * 8.8,
        w: 15.0,
        d: isEnd ? 10.0 : 8.25,
        areaSqM: isEnd ? 142.27 : 123.75,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }

  // NW Double Block 2 (West of Open Space 2): West 208..215 | East 207..201
  plots.push(
    makePlot({ no: "208", x: -70, z: -102, w: 15.0, d: 18.0, areaSqM: 371.68, faces: "W", road: "9.00 m Wide Road", status: statusOf("208"), baseRate: base + 50, corner: true }),
    makePlot({ no: "207", x: -54, z: -96, w: 15.0, d: 16.0, areaSqM: 296.68, faces: "E", road: "9.00 m Wide Road", status: statusOf("207"), baseRate: base + 50, corner: true }),
  );
  for (let i = 0; i < 7; i++) {
    const no = String(209 + i);
    const isEnd = i === 6;
    plots.push(
      makePlot({
        no,
        x: -70,
        z: -85 + i * 8.4,
        w: 14.98,
        d: isEnd ? 9.5 : 7.25,
        areaSqM: isEnd ? 134.14 : 108.58,
        faces: "W",
        road: isEnd ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }
  for (let i = 0; i < 6; i++) {
    const no = String(206 - i);
    const isEnd = i === 5;
    plots.push(
      makePlot({
        no,
        x: -54,
        z: -77 + i * 8.4,
        w: 15.0,
        d: isEnd ? 9.5 : 7.25,
        areaSqM: isEnd ? 134.77 : 108.75,
        faces: "E",
        road: isEnd ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }

  // North-Central Horizontal Row (200 down to 188, facing 12m Spine, below Open Space 2)
  for (let i = 0; i < 13; i++) {
    const no = String(200 - i);
    const isFirst = i === 0;
    const isLast = i === 12;
    const w = isFirst ? 12.0 : isLast ? 14.0 : 8.7;
    plots.push(
      makePlot({
        no,
        x: -31 + i * 9.3 + (isFirst ? -1 : isLast ? 2.5 : 0),
        z: -35,
        w,
        d: 13.0,
        areaSqM: isFirst ? 148.27 : isLast ? 230.99 : 113.1,
        faces: "S",
        road: "12.00 m Wide Road",
        status: statusOf(no),
        baseRate: comm,
        category: "Commercial",
        corner: isFirst || isLast,
        parkFacing: true,
      }),
    );
  }

  // --- B. MAIN CENTRAL GRID (South of 12.00 m Spine, z > -10) ---
  // Far-West Strip: 187..184 (4 plots)
  [
    { no: "187", z: -4, w: 14.46, d: 9.5, area: 138.11 },
    { no: "186", z: 5.5, w: 16.15, d: 8.0, area: 128.73 },
    { no: "185", z: 14.2, w: 16.04, d: 8.0, area: 127.85 },
    { no: "184", z: 23.2, w: 15.93, d: 9.2, area: 147.89 },
  ].forEach((row, idx) => {
    plots.push(
      makePlot({
        no: row.no,
        x: -176,
        z: row.z,
        w: row.w,
        d: row.d,
        areaSqM: row.area,
        faces: "E",
        road: idx === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road",
        status: statusOf(row.no),
        baseRate: base,
        corner: idx === 0 || idx === 3,
      }),
    );
  });

  // Short Block A1: West 180..183 | East 179..176
  for (let i = 0; i < 4; i++) {
    const zPos = -4 + i * 9.2;
    const isEnd = i === 0 || i === 3;
    const d = isEnd ? 9.8 : 8.0;
    plots.push(
      makePlot({ no: String(180 + i), x: -150, z: zPos, w: 16.0, d, areaSqM: i === 0 ? 152.27 : i === 3 ? 155.17 : 128.0, faces: "W", road: "9.00 m Wide Road", status: statusOf(String(180 + i)), baseRate: base, corner: isEnd }),
      makePlot({ no: String(179 - i), x: -133.5, z: zPos, w: 16.0, d, areaSqM: i === 0 ? 152.27 : i === 3 ? 155.17 : 128.0, faces: "E", road: "9.00 m Wide Road", status: statusOf(String(179 - i)), baseRate: base, corner: isEnd }),
    );
  }

  // Short Block A2: West 172..175 | East 171..168
  for (let i = 0; i < 4; i++) {
    const zPos = -4 + i * 9.2;
    const isEnd = i === 0 || i === 3;
    const d = isEnd ? 9.8 : 8.0;
    plots.push(
      makePlot({ no: String(172 + i), x: -107, z: zPos, w: 15.0, d, areaSqM: i === 0 ? 142.27 : i === 3 ? 145.48 : 120.0, faces: "W", road: "9.00 m Wide Road", status: statusOf(String(172 + i)), baseRate: base, corner: isEnd }),
      makePlot({ no: String(171 - i), x: -91.5, z: zPos, w: 15.0, d, areaSqM: i === 0 ? 142.27 : i === 3 ? 145.48 : 120.0, faces: "E", road: "9.00 m Wide Road", status: statusOf(String(171 - i)), baseRate: base, corner: isEnd }),
    );
  }

  // Block B: West Col 164..147 (18 plots) | Upper East Col 163..160 (4 plots)
  for (let i = 0; i < 4; i++) {
    const zPos = -4 + i * 9.2;
    const isEnd = i === 0 || i === 3;
    const d = isEnd ? 9.8 : 8.0;
    plots.push(
      makePlot({ no: String(164 + i), x: -66, z: zPos, w: 15.0, d, areaSqM: i === 0 ? 142.04 : i === 3 ? 145.25 : 119.82, faces: "W", road: "9.00 m Wide Road", status: statusOf(String(164 + i)), baseRate: base, corner: isEnd }),
      makePlot({ no: String(163 - i), x: -50.5, z: zPos, w: 15.0, d, areaSqM: i === 0 ? 142.27 : i === 3 ? 145.48 : 120.0, faces: "E", road: "9.00 m Wide Road", status: statusOf(String(163 - i)), baseRate: base, corner: isEnd }),
    );
  }
  // Continuing long single strip 159 down to 147 (13 plots facing East onto 9m Road)
  for (let i = 0; i < 13; i++) {
    const no = String(159 - i);
    const isEnd = i === 0 || i === 12;
    const d = isEnd ? 9.2 : 7.5;
    plots.push(
      makePlot({
        no,
        x: -50.5,
        z: 34 + i * 8.1,
        w: 16.0 - i * 0.1,
        d,
        areaSqM: i === 0 ? 152.73 : i === 12 ? 143.02 : 120.18,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: i === 12,
      }),
    );
  }

  // Block C: West 146..131 (16 plots) | East 115..130 (16 plots)
  for (let i = 0; i < 16; i++) {
    const zPos = -4 + i * 8.85;
    const isEnd = i === 0 || i === 15;
    const d = isEnd ? 10.2 : 8.2;
    const wNo = String(146 - i);
    const eNo = String(115 + i);
    plots.push(
      makePlot({ no: wNo, x: -24, z: zPos, w: 14.75, d, areaSqM: isEnd ? 147.15 : 120.95, faces: "W", road: i === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road", status: statusOf(wNo), baseRate: base, corner: isEnd }),
      makePlot({ no: eNo, x: -8.8, z: zPos, w: 14.75, d, areaSqM: isEnd ? 147.15 : 120.95, faces: "E", road: i === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road", status: statusOf(eNo), baseRate: base, corner: isEnd }),
    );
  }

  // Block D: West 114..100 (15 plots) | East 85..99 (15 plots, fronting 12m N-S Spine)
  for (let i = 0; i < 15; i++) {
    const zPos = -4 + i * 9.35;
    const isEnd = i === 0 || i === 14;
    const d = isEnd ? 10.5 : 8.6;
    const wNo = String(114 - i);
    const eNo = String(85 + i);
    plots.push(
      makePlot({ no: wNo, x: 18, z: zPos, w: 14.75, d, areaSqM: isEnd ? 149.5 : 126.85, faces: "W", road: i === 0 ? "12.00 m Wide Road" : "9.00 m Wide Road", status: statusOf(wNo), baseRate: i === 0 ? comm : base, corner: isEnd }),
      makePlot({ no: eNo, x: 33.5, z: zPos, w: 15.0, d, areaSqM: isEnd ? 152.0 : 129.0, faces: "E", road: "12.00 m Wide Road", status: statusOf(eNo), baseRate: comm, category: "Commercial", corner: isEnd }),
    );
  }

  // Block E (East of 12m N-S Spine): West 84..72 (13 plots) | East 60..71 (12 plots)
  for (let i = 0; i < 13; i++) {
    const no = String(84 - i);
    const isEnd = i === 0 || i === 12;
    plots.push(
      makePlot({
        no,
        x: 61,
        z: -4 + i * 9.1,
        w: 14.0,
        d: isEnd ? 10.5 : 8.4,
        areaSqM: i === 0 ? 138.36 : i === 12 ? 189.13 : 117.6,
        faces: "W",
        road: "12.00 m Wide Road",
        status: statusOf(no),
        baseRate: comm,
        category: "Commercial",
        corner: isEnd,
      }),
    );
  }
  for (let i = 0; i < 12; i++) {
    const no = String(60 + i);
    const isEnd = i === 0 || i === 11;
    plots.push(
      makePlot({
        no,
        x: 75.5,
        z: -4 + i * 9.1,
        w: 14.0,
        d: isEnd ? 10.5 : 8.4,
        areaSqM: i === 0 ? 138.36 : i === 11 ? 166.45 : 117.6,
        faces: "E",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }

  // East Strip along Open Space 3: 59 down to 51 (9 plots)
  for (let i = 0; i < 9; i++) {
    const no = String(59 - i);
    const isEnd = i === 0 || i === 8;
    plots.push(
      makePlot({
        no,
        x: 100,
        z: -1 + i * 9.2,
        w: 13.35,
        d: isEnd ? 11.5 : 7.5,
        areaSqM: i === 0 ? 324.24 : i === 8 ? 161.93 : 100.13,
        faces: "W",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base + 50,
        corner: isEnd,
        parkFacing: true,
      }),
    );
  }

  // --- C. SOUTH HORIZONTAL BLOCK (below 9m Road, West of 12m Spine) ---
  // Top Row: 24..35 (12 plots facing N)
  for (let i = 0; i < 12; i++) {
    const no = String(24 + i);
    const isEnd = i === 0 || i === 11;
    const w = isEnd ? 9.6 : 7.25;
    plots.push(
      makePlot({
        no,
        x: -51 + i * 8.1,
        z: 152,
        w,
        d: 14.0,
        areaSqM: i === 0 ? 137.05 : i === 11 ? 131.57 : 101.5,
        faces: "N",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base,
        corner: isEnd,
      }),
    );
  }
  // Bottom Row: 23..16 (8 larger detached plots facing S)
  for (let i = 0; i < 8; i++) {
    const no = String(23 - i);
    plots.push(
      makePlot({
        no,
        x: -47 + i * 11.8,
        z: 171,
        w: 11.0,
        d: 20.5,
        areaSqM: i === 0 ? 201.62 : i === 7 ? 233.95 : 228.0,
        faces: "S",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base + 80,
        corner: i === 0 || i === 7,
      }),
    );
  }

  // --- D. SOUTH-EAST ANGLED DIAGONAL WING (tilted -38 deg) ---
  // NW Row (36..50, 15 plots) & SE Row (15..1, 15 plots)
  const angleDeg = -38;
  const rad = (angleDeg * Math.PI) / 180;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);
  const originX = 62;
  const originZ = 148;

  // NW row (36 to 50)
  for (let i = 0; i < 15; i++) {
    const no = String(36 + i);
    const sAlong = i * 9.0;
    const sPerp = -10.5;
    const wx = originX + sAlong * cosA - sPerp * sinA;
    const wz = originZ + sAlong * sinA + sPerp * cosA;
    plots.push(
      makePlot({
        no,
        x: wx,
        z: wz,
        w: i === 0 || i === 14 ? 10.2 : 8.0,
        d: 14.0,
        rotDeg: angleDeg,
        areaSqM: i === 0 ? 160.41 : i === 14 ? 135.2 : 112.0,
        faces: "N",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base + 50,
        corner: i === 0 || i === 14,
      }),
    );
  }

  // SE row (15 down to 1 — 1 to 10 are SOLD!)
  for (let i = 0; i < 15; i++) {
    const no = String(15 - i);
    const sAlong = i * 10.6;
    const sPerp = 10.0;
    const wx = originX + sAlong * cosA - sPerp * sinA;
    const wz = originZ + sAlong * sinA + sPerp * cosA;
    plots.push(
      makePlot({
        no,
        x: wx,
        z: wz,
        w: 10.25,
        d: 20.5,
        rotDeg: angleDeg,
        areaSqM: i === 0 ? 256.29 : 210.0,
        faces: "S",
        road: "9.00 m Wide Road",
        status: statusOf(no),
        baseRate: base + 100,
        corner: i === 0 || i === 14,
      }),
    );
  }

  return {
    bounds: { x: 2, z: 12, w: 420, d: 370 },
    roads: [
      { x: -32, z: -18, w: 325, d: 12, label: "12.00 m Wide Road", orient: "h" },
      { x: 47.5, z: 75, w: 12, d: 196, label: "12.00 m Wide Road", orient: "v" },
      { x: -125, z: -85, w: 9, d: 155, label: "9.00 m Road", orient: "v" },
      { x: -83, z: -72, w: 9, d: 120, label: "9.00 m Road", orient: "v" },
      { x: -38, z: 58, w: 9, d: 160, label: "9.00 m Wide Road", orient: "v" },
      { x: 4.5, z: 58, w: 9, d: 160, label: "9.00 m Wide Road", orient: "v" },
      { x: 88, z: 36, w: 9, d: 112, label: "9.00 m Wide Road", orient: "v" },
      { x: -10, z: 138, w: 120, d: 9, label: "9.00 m Wide Road", orient: "h" },
      { x: 114, z: 108, w: 175, d: 9, rotDeg: -38, label: "9.00 m Wide Road", orient: "h" },
      { x: 140, z: 134, w: 175, d: 9, rotDeg: -38, label: "9.00 m Wide Road", orient: "h" },
    ],
    zones: [
      { x: -158, z: -175, w: 28, d: 20, label: "Open Space 1 · 537.36 sq.m", kind: "open" },
      { x: -168, z: -100, w: 34, d: 95, label: "Amenity Space 2 · 3,989.73 sq.m", kind: "amenity" },
      { x: 24, z: -75, w: 115, d: 44, label: "Open Space 2 · 4,111.99 sq.m", kind: "open" },
      { x: 124, z: 24, w: 30, d: 62, label: "Open Space 3 · 1,721.02 sq.m", kind: "open" },
      { x: 186, z: 62, w: 50, d: 42, label: "Amenity Space 1 · 2,365.09 sq.m", kind: "amenity" },
    ],
    plots,
  };
}

// ============================================================================
// 4. ANANTA & AKASHA — KH. NO. 64 & 64/2 ADJ. SANCTIONED NATP KH. NO. 66/2
// Reference: Marked 2D Layout Plan Image ("ANANTA & AKASHA")
// Circled plot numbers = SOLD OUT (47 plots); Un-circled = AVAILABLE (7 plots: K-01, K-04, K-05, K-17, K-22, K-23, K-26)
// ============================================================================
function buildAnantaAkashaLayout(): ProjectLayout {
  const base = 2650;
  const plots: PlotCell[] = [];

  // --- WEST WING: ANANTA (Plots 1 to 28 — ALL 28 circled SOLD OUT on attached sheet) ---
  // Far-West Column (West of 9.00 m Road, South to North: 1 to 14)
  for (let i = 1; i <= 14; i++) {
    const no = `A-${String(i).padStart(2, "0")}`;
    const isBottom = i === 1;
    const zPos = isBottom ? 44 : 33 - (i - 2) * 7.8;
    const d = isBottom ? 13.5 : 7.2;
    plots.push(
      makePlot({
        no,
        displayNo: String(i),
        wing: "Ananta Wing",
        x: -76,
        z: zPos,
        w: 14.5,
        d,
        areaSqM: isBottom ? 195.8 : 104.4,
        faces: "E",
        road: isBottom ? "30.00 m Wide Road" : "9.00 m Wide Road",
        status: "sold",
        baseRate: isBottom ? base + 250 : base,
        corner: isBottom || i === 14,
      }),
    );
  }

  // Middle-West Column (East of 9.00 m Road, below Open Space, North to South: 15 to 24)
  for (let i = 15; i <= 24; i++) {
    const no = `A-${String(i).padStart(2, "0")}`;
    const idx = i - 15;
    const isBottom = i === 24;
    const zPos = isBottom ? 36 : -34 + idx * 7.2;
    const d = isBottom ? 22.0 : 6.7;
    plots.push(
      makePlot({
        no,
        displayNo: String(i),
        wing: "Ananta Wing",
        x: -51,
        z: zPos,
        w: 13.5,
        d,
        areaSqM: isBottom ? 297.0 : 90.5,
        faces: "W",
        road: isBottom ? "30.00 m Wide Road" : "9.00 m Wide Road",
        status: "sold",
        baseRate: isBottom ? base + 250 : base,
        corner: isBottom || i === 15,
        parkFacing: i <= 17,
      }),
    );
  }

  // Inner-West Column (facing 18.00 m Wide Road, South to North: 25 to 28)
  [
    { num: 25, z: 36, d: 22.0, area: 330.0, corner: true },
    { num: 26, z: 16, d: 15.0, area: 225.0, corner: false },
    { num: 27, z: 0, d: 14.5, area: 217.5, corner: false },
    { num: 28, z: -15.5, d: 14.5, area: 217.5, corner: true },
  ].forEach((row) => {
    plots.push(
      makePlot({
        no: `A-${row.num}`,
        displayNo: String(row.num),
        wing: "Ananta Wing",
        x: -36,
        z: row.z,
        w: 15.0,
        d: row.d,
        areaSqM: row.area,
        faces: "E",
        road: row.num === 25 ? "30.00 m Wide Road" : "18.00 m Wide Road",
        status: "sold",
        baseRate: base + 150,
        corner: row.corner,
        parkFacing: row.num === 28,
      }),
    );
  });

  // --- EAST WING: AKASHA (Plots 1 to 26 — Un-circled AVAILABLE: 1, 4, 5, 17, 22, 23, 26) ---
  const akashaAvailable = new Set([1, 4, 5, 17, 22, 23, 26]);
  const akStatus = (n: number): PlotStatus => (akashaAvailable.has(n) ? "available" : "sold");

  // West sub-block of Akasha (East of 18.00 m Road): 1, 2, 3, 4
  plots.push(
    makePlot({ no: "K-01", displayNo: "1", wing: "Akasha Wing", x: 1, z: 17, w: 28.0, d: 16.5, areaSqM: 462.0, faces: "W", road: "18.00 m Wide Road", status: akStatus(1), baseRate: base + 200, corner: true }),
    makePlot({ no: "K-02", displayNo: "2", wing: "Akasha Wing", x: -4.5, z: 2.5, w: 17.5, d: 12.0, areaSqM: 210.0, faces: "W", road: "18.00 m Wide Road", status: akStatus(2), baseRate: base + 150 }),
    makePlot({ no: "K-03", displayNo: "3", wing: "Akasha Wing", x: -4.5, z: -11, w: 17.5, d: 13.0, areaSqM: 227.5, faces: "W", road: "18.00 m Wide Road", status: akStatus(3), baseRate: base + 180, corner: true }),
    makePlot({ no: "K-04", displayNo: "4", wing: "Akasha Wing", x: 10.5, z: -4, w: 11.5, d: 24.0, areaSqM: 276.0, faces: "N", road: "9.00 m Wide Road", status: akStatus(4), baseRate: base + 120, parkFacing: true }),
  );

  // North of Angled 9.00 m Wide Road (tilted +16 deg clockwise): 5 (large prime corner), 6..16 (SOLD), 17 (AVAIL)
  const eastTilt = 16;
  const eRad = (eastTilt * Math.PI) / 180;
  const cosE = Math.cos(eRad);
  const sinE = Math.sin(eRad);
  const roadAnchorX = 16;
  const roadAnchorZ = -24;

  // Plot 5 (large prime corner fronting 18m & 9m road — AVAILABLE!)
  plots.push(
    makePlot({
      no: "K-05",
      displayNo: "5",
      wing: "Akasha Wing",
      x: 1,
      z: -33,
      w: 27.0,
      d: 15.5,
      rotDeg: eastTilt,
      areaSqM: 418.5,
      faces: "S",
      road: "18.00 m & 9.00 m Wide Road",
      status: akStatus(5),
      baseRate: base + 250,
      corner: true,
    }),
  );

  // Plots 6 to 16 (all 11 circled SOLD) + Plot 17 (AVAILABLE!) along angled 9m Road
  for (let n = 6; n <= 17; n++) {
    const idx = n - 6;
    const isLast = n === 17;
    const sAlong = 4 + idx * 8.15 + (isLast ? 3.0 : 0);
    const sPerp = -13.5;
    const wx = roadAnchorX + sAlong * cosE - sPerp * sinE;
    const wz = roadAnchorZ + sAlong * sinE + sPerp * cosE;
    const w = isLast ? 14.0 : 7.6;
    plots.push(
      makePlot({
        no: `K-${String(n).padStart(2, "0")}`,
        displayNo: String(n),
        wing: "Akasha Wing",
        x: wx,
        z: wz,
        w,
        d: 14.5,
        rotDeg: eastTilt,
        areaSqM: isLast ? 203.0 : 110.2,
        faces: "S",
        road: "9.00 m Wide Road",
        status: akStatus(n),
        baseRate: isLast ? base + 120 : base,
        corner: isLast,
      }),
    );
  }

  // South of Angled 9.00 m Wide Road (East of Open Space 1): 18..21 (SOLD), 22 & 23 (AVAILABLE!)
  for (let n = 18; n <= 23; n++) {
    const idx = n - 18;
    const sAlong = 38 + idx * 9.3;
    const sPerp = 10.5;
    const wx = roadAnchorX + sAlong * cosE - sPerp * sinE;
    const wz = roadAnchorZ + sAlong * sinE + sPerp * cosE;
    plots.push(
      makePlot({
        no: `K-${n}`,
        displayNo: String(n),
        wing: "Akasha Wing",
        x: wx,
        z: wz,
        w: 8.6,
        d: 15.0,
        rotDeg: eastTilt,
        areaSqM: 129.0,
        faces: "N",
        road: "9.00 m Wide Road",
        status: akStatus(n),
        baseRate: base + 50,
        parkFacing: n === 18,
      }),
    );
  }

  // South Frontage Estate Parcels facing 30.00 m Wide Road: 26 (AVAILABLE!), 25 (SOLD), 24 (SOLD)
  plots.push(
    makePlot({ no: "K-26", displayNo: "26", wing: "Akasha Wing", x: 33, z: 27, w: 28.0, d: 28.0, areaSqM: 455.7, faces: "S", road: "30.00 m Wide Road", status: akStatus(26), baseRate: base + 350, corner: true, parkFacing: true }),
    makePlot({ no: "K-25", displayNo: "25", wing: "Akasha Wing", x: 61, z: 25, w: 24.0, d: 25.0, areaSqM: 420.0, faces: "S", road: "30.00 m Wide Road", status: akStatus(25), baseRate: base + 300 }),
    makePlot({ no: "K-24", displayNo: "24", wing: "Akasha Wing", x: 84, z: 27, w: 21.0, d: 20.0, areaSqM: 380.0, faces: "S", road: "30.00 m Wide Road", status: akStatus(24), baseRate: base + 300, corner: true }),
  );

  return {
    bounds: { x: 16, z: 2, w: 222, d: 146 },
    roads: [
      { x: 14, z: 59, w: 214, d: 20, rotDeg: -5, label: "30.00 m Wide Road", orient: "h" },
      { x: -20, z: -2, w: 14, d: 112, label: "18.00 m Wide Road", orient: "v" },
      { x: -63.5, z: -4, w: 9, d: 114, label: "9.00 m Wide Road", orient: "v" },
      { x: 52, z: -13, w: 122, d: 8.5, rotDeg: 16, label: "9.00 m Wide Road", orient: "h" },
    ],
    zones: [
      { x: -43, z: -46, w: 28, d: 22, label: "Open Space", kind: "open" },
      { x: 32, z: -2, w: 26, d: 24, label: "Open Space 1", kind: "open" },
      { x: 1, z: 36, w: 26, d: 18, label: "Vacant Space", kind: "vacant" },
    ],
    plots,
  };
}

export const PROJECTS: EstateProject[] = [
  {
    slug: "samruddhi-estate-phase-3",
    name: "Samruddhi Estate Phase 3",
    corridor: "Salai Dabha · Hingna – MIHAN",
    location: "Mouza Salaidabha, Tah. Hingna, Dist. Nagpur",
    district: "Nagpur",
    state: "Maharashtra",
    khNo: "Kh. No. 110",
    mouza: "Mouza Salaidabha",
    heroImage: "/website/src/assets/images/hero_nagpur_estate_1795287010697.jpg",
    tagline: "NMRDA approved plots in Salai Dabha near Hingna & MIHAN. Residential from ₹1,700/sq.ft* & Commercial from ₹1,750/sq.ft*.",
    headline: "Samruddhi Estate Phase 3,",
    headlineEm: "ready plots in Salai Dabha.",
    intro:
      "NMRDA sanctioned layout in Mouza Salai Dabha, Nagpur. 74 of 76 plots are already sold, with Plots 12 and 34 currently open for booking.",
    emotionalPitch:
      "Buying in a neighborhood where 74 families have already booked gives you complete peace of mind. Wide concrete roads, open park space, and clear 7/12 papers make this a safe choice for your home.",
    frontage: "12.00 m Main Road · 9.00 m Internal Roads",
    baseRatePerSqFt: 1700,
    commercialRatePerSqFt: 1750,
    idealFor: ["Government Officers", "Families Building a Home", "Safe Land Investors"],
    compliance: {
      authority: "NMRDA · Nagpur Metropolitan Region Development Authority",
      sanctionRef: "Sanctioned Agreement Dt. 02/07/2026 · Kh. No. 110",
      digitalSignStamp: "NMRDA Sanction Seal Verified",
      phNo: "P.H. No. 70",
      regulationRef: "D.P. Sheet No. 117 · UDCPR-2020",
      proRataFsi: "1.78 (18,311.48 / 10,263.62)",
      openAndAmenity: "1,931.19 + 964.26 sq.m",
      reraStatus: "Verify MahaRERA Portal Ref on Site Visit",
    },
    facts: [
      { label: "Total Area", value: "19,275 m²", note: "76 sanctioned plots (01–76) on clear layout" },
      { label: "Park & Amenities", value: "2,895 m²", note: "Dedicated Open Park, Amenity Zone & E.S.S." },
      { label: "Starting Rate*", value: "₹1,700 / ₹1,750", note: "Residential ₹1,700/sq.ft* · Commercial ₹1,750/sq.ft*" },
    ],
    claims: ["NMRDA Approved Layout", "12 m Main & 9 m Internal Roads", "Dedicated Open Park", "Bank Loan Eligible"],
    landmarks: [
      { name: "Mouza Salai Dabha", note: "Adjoining Phase 4 in Hingna’s active growth corridor" },
      { name: "MIHAN SEZ & IT Hub", note: "Easy daily commute to Nagpur’s main job center" },
      { name: "Wardha Road & Airport", note: "Quick drive to Nagpur Airport and AIIMS" },
      { name: "Outer Ring Road", note: "Smooth connectivity across Hingna and Butibori" },
    ],
    faqs: [
      {
        question: "Which plots are open right now in Samruddhi Estate Phase 3?",
        answer:
          "74 out of 76 plots are already sold out. Plots 12 and 34 are open for booking. Tap them on the 3D map to check exact size, road width, and EMI.",
      },
      {
        question: "Can I see plot dimensions and Vastu direction before visiting?",
        answer:
          "Yes. Click any plot on the 3D or 2D map to open its 360° view with exact meter/feet sizes, road width, and live compass direction.",
      },
      {
        question: "Are bank loans available for Salai Dabha NMRDA plots?",
        answer:
          "Yes. SBI, HDFC, ICICI, and Bank of Baroda offer plot loans on NMRDA-sanctioned layouts, subject to standard bank checks.",
      },
    ],
    layout: buildSamruddhiPhase3Layout(),
  },
  {
    slug: "samruddhi-estate-phase-4",
    name: "Samruddhi Estate Phase 4",
    corridor: "Salai Dabha · 18 m D.P. Road",
    location: "Mouza Salaidabha, Tah. Hingna, Dist. Nagpur",
    district: "Nagpur",
    state: "Maharashtra",
    khNo: "Kh. No. 111",
    mouza: "Mouza Salaidabha",
    heroImage: "/website/src/assets/images/project_sajal_wardha_1795287010087.jpg",
    tagline: "Wide 18 m D.P. Road layout in Salai Dabha with 55 open plots. Residential from ₹1,700/sq.ft* & Commercial from ₹1,750/sq.ft*.",
    headline: "Samruddhi Estate Phase 4,",
    headlineEm: "on an 18 m wide D.P. road.",
    intro:
      "Located right next to Phase 3 in Salai Dabha, Nagpur. Choose from 55 open residential and commercial plots with dual parks and clear wide-road access.",
    emotionalPitch:
      "Wide 18-meter main roads and large green parks protect your family’s comfort and boost your land’s future resale value. Phase 4 gives you an easy-to-reach Nagpur address built for generations.",
    frontage: "18.00 m Wide D.P. Road · 9.00 m Internal Roads",
    baseRatePerSqFt: 1700,
    commercialRatePerSqFt: 1750,
    idealFor: ["Doctors & Professionals", "Government Officers", "NRI & Family Buyers"],
    compliance: {
      authority: "NMRDA · Nagpur Metropolitan Region Development Authority",
      sanctionRef: "Proposed Residential Layout Plan · Kh. No. 111",
      digitalSignStamp: "UDCPR-2020 Area Statement Verified",
      phNo: "P.H. No. 70",
      regulationRef: "D.P. Sheet No. 117 · UDCPR-2020",
      proRataFsi: "1.60 (18,787.55 / 11,735.50)",
      openAndAmenity: "2,001.28 + 1,000.32 sq.m",
      reraStatus: "Verify MahaRERA Portal Ref on Site Visit",
    },
    facts: [
      { label: "Total Area", value: "20,049 m²", note: "83 total plots · 55 Open, 28 Sold Out" },
      { label: "Main Road Width", value: "18.00 m D.P.", note: "Direct entry from 18 m Development Plan road" },
      { label: "Starting Rate*", value: "₹1,700 / ₹1,750", note: "Residential ₹1,700/sq.ft* · Commercial ₹1,750/sq.ft*" },
    ],
    claims: ["18 m Wide D.P. Road Entry", "2 Landscaped Parks", "55 Open Plots Ready", "Bank Loan Eligible"],
    landmarks: [
      { name: "18.00 m D.P. Road", note: "Wide direct road entry for easy car & commercial access" },
      { name: "Next to Phase 3", note: "Part of the larger Samruddhi Estate neighborhood" },
      { name: "MIHAN, AIIMS & IIM", note: "Close to Nagpur’s top medical, tech & college belt" },
      { name: "Nagpur Airport Link", note: "Smooth drive along the Hingna–Wardha belt" },
    ],
    faqs: [
      {
        question: "How many plots are available in Samruddhi Estate Phase 4?",
        answer:
          "Out of 83 total plots, 28 are sold out and 55 residential and corner plots are open right now. Click any green plot on the 3D map to inspect.",
      },
      {
        question: "What are the residential and commercial plot rates in Phase 4?",
        answer:
          "Residential plots start at ₹1,700/sq.ft* and main-road Commercial plots start at ₹1,750/sq.ft*. Request an official written cost sheet before booking.",
      },
      {
        question: "Can I take a bank loan to buy a plot here?",
        answer:
          "Yes. Leading banks like SBI, HDFC, ICICI, and Bank of Baroda finance up to 70%–75% on approved Nagpur RL plots, subject to eligibility.",
      },
    ],
    layout: buildSamruddhiPhase4Layout(),
  },
  {
    slug: "bhagirath-nagari",
    name: "Bhagirathi Nagar - 4",
    corridor: "Kanholi · Opposite Gurudev Zagekar Plot",
    location: "Mouza Kanholi (Opp. Gurudev Zagekar Plot, X2C5+96), Tah. Hingna, Nagpur",
    district: "Nagpur",
    state: "Maharashtra",
    khNo: "Sr. No. 87/1",
    mouza: "Mouza Kanholi",
    heroImage: "/website/src/assets/images/project_samruddhi_butibori_1795287027780.jpg",
    tagline: "15.69-acre NMRDA digitally signed township in Kanholi. Residential from ₹1,990/sq.ft* & Commercial from ₹2,050/sq.ft*.",
    headline: "Bhagirathi Nagar - 4,",
    headlineEm: "15.69 acres of planned living.",
    intro:
      "Located directly opposite Gurudev Zagekar Plot (X2C5+96) in Mouza Kanholi, Hingna. Features 253 clear plots, 12 m wide roads, 3 green parks, and NMRDA digital sanction (NMRDA-26-36337).",
    emotionalPitch:
      "Small unplanned layouts often lack parks, wide roads, and proper drainage. Bhagirathi Nagar - 4 gives your family a complete 15.69-acre township with three green parks and verified NMRDA papers.",
    frontage: "12.00 m Main Spine Roads · 9.00 m Internal Grid",
    baseRatePerSqFt: 1990,
    commercialRatePerSqFt: 2050,
    idealFor: ["Long-Term Investors", "Families Buying a Home Plot", "Shop & Commercial Buyers"],
    compliance: {
      authority: "NMRDA · Digitally Signed Submission Drawing",
      sanctionRef: "File Ref: NMRDA-26-36337 · Dt. 12/06/2026",
      digitalSignStamp: "NMRDA Digital Signature Affixed",
      phNo: "P.H. No. 48",
      regulationRef: "UDCPR-2020 Reg. 3.4 & 3.5",
      proRataFsi: "1.654 (Basic FSI 1.100)",
      openAndAmenity: "6,370.38 + 6,354.83 sq.m",
      reraStatus: "Tentative Layout · Confirm MahaRERA Status",
    },
    facts: [
      { label: "Township Size", value: "15.69 Acres", note: "63,491 sq.m. total area · 253 surveyed plots" },
      { label: "Starting Rate*", value: "₹1,990 / ₹2,050", note: "Residential ₹1,990/sq.ft* · Commercial ₹2,050/sq.ft*" },
      { label: "Parks & Amenities", value: "12,725 m²", note: "3 Open Parks, 2 Amenity Zones & Power Sub-Station" },
    ],
    claims: ["NMRDA Ref: NMRDA-26-36337", "Res. ₹1,990* · Com. ₹2,050*", "3 Landscaped Parks", "12 m Wide Roads"],
    landmarks: [
      { name: "Opposite Gurudev Zagekar Plot", note: "Exact Kanholi site landmark (X2C5+96, Nagpur 441108)" },
      { name: "Hingna – Butibori Belt", note: "Fast-growing industrial, college & residential corridor" },
      { name: "Samruddhi & Outer Ring Road", note: "Quick access to Nagpur’s highway network" },
      { name: "MIHAN & Wardha Road", note: "Easy drive to Nagpur’s biggest employment hub" },
    ],
    faqs: [
      {
        question: "Where is Bhagirathi Nagar - 4 located in Nagpur?",
        answer:
          "It is located directly opposite Gurudev Zagekar Plot (X2C5+96), Mouza Kanholi, Tah. Hingna, Nagpur 441108. You can view the exact location on our live 3D Satellite map above.",
      },
      {
        question: "What are the residential and commercial starting rates?",
        answer:
          "Residential plots start at ₹1,990/sq.ft* and 12 m wide-road Commercial plots start at ₹2,050/sq.ft*. Click any plot in 3D to estimate total cost and EMI.",
      },
      {
        question: "Does Bhagirathi Nagar - 4 have NMRDA approval?",
        answer:
          "Yes. It carries digitally signed NMRDA file reference NMRDA-26-36337 under UDCPR-2020 norms. Full sanction copies are shared during your site visit.",
      },
    ],
    layout: buildBhagirathNagariLayout(),
  },
  {
    slug: "ananta-akasha",
    name: "Ananta & Akasha",
    corridor: "Dongargaon · 30 m Boulevard",
    location: "At Satish Electricals Branch 2 (X2RH+PF), Dongargaon, Nagpur 441108",
    district: "Nagpur",
    state: "Maharashtra",
    khNo: "Kh. No. 64 & 64/2",
    mouza: "Mouza Dongargaon",
    heroImage: "/website/src/assets/images/project_ananta_hingna_1795287009980.jpg",
    tagline: "Premium dual-wing estate right on a 30 m Wide Road in Dongargaon. Only 7 Akasha plots open now.",
    headline: "Ananta & Akasha,",
    headlineEm: "luxury plots on a 30 m road.",
    intro:
      "Situated directly at Satish Electricals Branch 2 (X2RH+PF), Dongargaon, Nagpur. The entire Ananta Wing is sold out; only 7 prime plots remain open in Akasha Wing.",
    emotionalPitch:
      "True luxury is wide space and peaceful surroundings. A grand 30-meter front road, an 18-meter central boulevard, and large villa plots make Ananta & Akasha a rare Nagpur address.",
    frontage: "30.00 m Wide Road · 18.00 m Central Boulevard",
    baseRatePerSqFt: 2650,
    idealFor: ["Custom Villa Builders", "Business Owners", "Premium Land Buyers"],
    compliance: {
      authority: "NATP / Town Planning · Nagpur Belt",
      sanctionRef: "Kh. No. 64 & 64/2 · Adj. Sanctioned NATP Kh. No. 66/2",
      digitalSignStamp: "Sanctioned NATP Adjoining Corridor",
      phNo: "Kh. No. 64 / 64/2",
      regulationRef: "30 m & 18 m Road Network · UDCPR-2020",
      proRataFsi: "As per Sanctioned Layout Order",
      openAndAmenity: "Dual Landscaped Open Spaces + Vacant Zone",
      reraStatus: "Verify Applicable MahaRERA Ref Before Booking",
    },
    facts: [
      { label: "Grand Roads", value: "30 m + 18 m", note: "Direct 30 m main road & 18 m center avenue" },
      { label: "Open Plots", value: "7 of 54 Left", note: "Ananta Sold Out · Akasha 1, 4, 5, 17, 22, 23, 26 Open" },
      { label: "Starting Rate*", value: "₹2,650 / sq.ft", note: "Large villa & corner plots up to 4,905 sq.ft" },
    ],
    claims: ["30 m Wide Main Road", "18 m Central Boulevard", "Exact Dongargaon Site", "Only 7 Plots Remaining"],
    landmarks: [
      { name: "Satish Electricals Branch 2 (X2RH+PF)", note: "Exact site location in Dongargaon, Nagpur 441108" },
      { name: "30.00 m Main Boulevard", note: "Highway-width front road right outside the estate" },
      { name: "Hingna & College Belt", note: "Close to major colleges, hospitals & markets" },
      { name: "MIHAN & Samruddhi Access", note: "Quick drive to airport and expressway" },
    ],
    faqs: [
      {
        question: "Where is Ananta & Akasha situated?",
        answer:
          "The site is situated right at Satish Electricals Branch 2, X2RH+PF, Dongargaon, Maharashtra 441108. Inspect the live 3D Satellite map above for exact road access.",
      },
      {
        question: "Which plots are still available to buy?",
        answer:
          "All 28 plots in Ananta Wing are sold out. Only 7 plots in Akasha Wing — Plots 1, 4, 5, 17, 22, 23, and 26 — are currently open.",
      },
      {
        question: "Can I check sizes and road widths of all 7 open plots?",
        answer:
          "Yes. Tap any green plot in the 3D layout to open its 360° inspector with exact meter/feet dimensions, road width, compass facing, and EMI calculator.",
      },
    ],
    layout: buildAnantaAkashaLayout(),
  },
];

// Maintain legacy slug aliases so existing bookmarks or shared links resolve cleanly
const LEGACY_SLUG_MAP: Record<string, string> = {
  "sadaj-110": "samruddhi-estate-phase-3",
  "sajal-111": "samruddhi-estate-phase-4",
  "samruddhi-estate": "bhagirath-nagari",
  "bhagirathi-nagar-4": "bhagirath-nagari",
};

export function getProjectBySlug(slug: string): EstateProject | undefined {
  const canonical = LEGACY_SLUG_MAP[slug] ?? slug;
  return PROJECTS.find((p) => p.slug === canonical);
}

export function summarizeLayout(layout: ProjectLayout) {
  let available = 0;
  let reserved = 0;
  let sold = 0;
  for (const p of layout.plots) {
    if (p.status === "available") available++;
    else if (p.status === "reserved") reserved++;
    else sold++;
  }
  return { total: layout.plots.length, available, reserved, sold };
}
