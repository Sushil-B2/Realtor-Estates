import { FACING_LABEL, STATUS_META, type PlotCell, type ProjectLayout } from "../data/projects";

type Props = {
  layout: ProjectLayout;
  selectedPlotNo: string | null;
  statusFilter?: "all" | "available" | "reserved" | "sold";
  onSelectPlot?: (plot: PlotCell) => void;
  interactive?: boolean;
};

function arrowPoints(p: PlotCell): string {
  const s = Math.min(Math.min(p.w, p.d) * 0.18, 2.0);
  const { x, z } = p;
  switch (p.faces) {
    case "N":
      return `${x},${z - p.d / 2 - s * 0.7} ${x - s},${z - p.d / 2 + s * 0.7} ${x + s},${z - p.d / 2 + s * 0.7}`;
    case "S":
      return `${x},${z + p.d / 2 + s * 0.7} ${x - s},${z + p.d / 2 - s * 0.7} ${x + s},${z + p.d / 2 - s * 0.7}`;
    case "E":
      return `${x + p.w / 2 + s * 0.7},${z} ${x + p.w / 2 - s * 0.7},${z - s} ${x + p.w / 2 - s * 0.7},${z + s}`;
    case "W":
      return `${x - p.w / 2 - s * 0.7},${z} ${x - p.w / 2 + s * 0.7},${z - s} ${x - p.w / 2 + s * 0.7},${z + s}`;
  }
}

export default function LayoutMap2D({
  layout,
  selectedPlotNo,
  statusFilter = "all",
  onSelectPlot,
  interactive = true,
}: Props) {
  const { bounds, plots, roads, zones } = layout;
  const minX = bounds.x - bounds.w / 2;
  const minZ = bounds.z - bounds.d / 2;
  const isDense = plots.length > 120;
  const compassScale = Math.max(1, bounds.w / 180);

  return (
    <svg
      className="block h-auto max-h-[70vh] w-full rounded-xl border border-black/10 bg-[#ECEAE0] dark:border-white/10 dark:bg-[#0D1612]"
      viewBox={`${minX} ${minZ} ${bounds.w} ${bounds.d}`}
      role={interactive ? "group" : "img"}
      aria-label={`Interactive 2D layout sheet showing ${plots.length} residential plots with North orientation and facing arrows.`}
      preserveAspectRatio="xMidYMid meet"
    >
      <rect
        x={minX}
        y={minZ}
        width={bounds.w}
        height={bounds.d}
        className="fill-[#E7E5DA] dark:fill-[#0E1813]"
      />

      {/* Open spaces, amenity zones, vacant zones */}
      {zones.map((z, idx) => (
        <g
          key={`zone-${idx}`}
          transform={z.rotDeg ? `rotate(${z.rotDeg} ${z.x} ${z.z})` : undefined}
        >
          <rect
            x={z.x - z.w / 2}
            y={z.z - z.d / 2}
            width={z.w}
            height={z.d}
            rx={1.2}
            fill={z.kind === "nala" ? "#2E5A70" : z.kind === "vacant" ? "#5A625D" : "#3F6D4D"}
            fillOpacity={0.34}
            stroke={z.kind === "nala" ? "#4E89A6" : z.kind === "vacant" ? "#8C958F" : "#6FA07A"}
            strokeWidth={0.5}
            strokeDasharray="2 1.2"
          />
          {z.w >= 16 ? (
            <text
              x={z.x}
              y={z.z}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-[#1E3B2B] dark:fill-[#CFE2D4]"
              fontSize={Math.max(2.8, Math.min(z.w * 0.1, 4.4))}
              fontWeight={600}
              letterSpacing={0.18}
            >
              {z.label}
            </text>
          ) : null}
        </g>
      ))}

      {/* Roads */}
      {roads.map((r, idx) => {
        const roadRot = r.rotDeg ?? 0;
        const textRot = r.orient === "v" ? roadRot - 90 : roadRot;
        return (
          <g key={`road-${idx}`}>
            <rect
              x={r.x - r.w / 2}
              y={r.z - r.d / 2}
              width={r.w}
              height={r.d}
              transform={roadRot ? `rotate(${roadRot} ${r.x} ${r.z})` : undefined}
              className="fill-[#2A332E] dark:fill-[#1A2420]"
            />
            <text
              x={r.x}
              y={r.z}
              textAnchor="middle"
              dominantBaseline="middle"
              transform={textRot ? `rotate(${textRot} ${r.x} ${r.z})` : undefined}
              className="fill-[#E7E2D2]"
              fontSize={isDense ? 3.4 : 2.9}
              fontWeight={600}
              letterSpacing={0.22}
            >
              {r.label}
            </text>
          </g>
        );
      })}

      {/* Individual Plot Parcels (supports rotated angled blocks + circled sold-out marker) */}
      {plots.map((p) => {
        const isSelected = selectedPlotNo === p.no;
        const muted = statusFilter !== "all" && p.status !== statusFilter;
        const meta = STATUS_META[p.status];
        const label = p.displayNo ?? p.no;
        const minSide = Math.min(p.w, p.d);
        const fontSize = Math.max(2.6, Math.min(minSide * 0.38, isDense ? 4.2 : 5.0));

        return (
          <g
            key={p.no}
            transform={p.rotDeg ? `rotate(${p.rotDeg} ${p.x} ${p.z})` : undefined}
            role={interactive ? "button" : undefined}
            tabIndex={interactive ? 0 : undefined}
            aria-label={`Plot ${label}${p.wing ? ` (${p.wing})` : ""}, ${meta.label}, ${FACING_LABEL[p.faces]}, ${p.areaSqFt} sq.ft, fronting ${p.road}`}
            aria-pressed={interactive ? isSelected : undefined}
            onClick={interactive ? () => onSelectPlot?.(p) : undefined}
            onKeyDown={
              interactive
                ? (ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      onSelectPlot?.(p);
                    }
                  }
                : undefined
            }
            className={interactive ? "cursor-pointer outline-none" : undefined}
            style={{ opacity: muted ? 0.2 : 1, transition: "opacity 180ms ease" }}
          >
            <rect
              x={p.x - p.w / 2}
              y={p.z - p.d / 2}
              width={p.w}
              height={p.d}
              rx={0.75}
              fill={meta.color}
              stroke={isSelected ? "#E5C77B" : "rgba(8, 16, 12, 0.55)"}
              strokeWidth={isSelected ? 1.55 : 0.45}
            />
            {/* Sold-out circle indicator echoing marked sanction sheet */}
            {p.status === "sold" && minSide >= 6.5 ? (
              <circle
                cx={p.x}
                cy={p.z}
                r={Math.min(minSide * 0.34, 3.6)}
                fill="none"
                stroke="#180D0A"
                strokeOpacity={0.5}
                strokeWidth={0.45}
                style={{ pointerEvents: "none" }}
              />
            ) : null}
            <text
              x={p.x}
              y={p.z}
              textAnchor="middle"
              dominantBaseline="central"
              fill="#0B140F"
              fontSize={fontSize}
              fontWeight={700}
              style={{ pointerEvents: "none" }}
            >
              {label}
            </text>
            {interactive && !isDense && minSide >= 10 ? (
              <text
                x={p.x}
                y={p.z + fontSize * 0.95}
                textAnchor="middle"
                dominantBaseline="central"
                fill="#112219"
                fontSize={1.8}
                fontWeight={600}
                style={{ pointerEvents: "none" }}
              >
                {p.faces}
              </text>
            ) : null}
            <polygon
              points={arrowPoints(p)}
              fill="#0B140F"
              opacity={0.75}
              style={{ pointerEvents: "none" }}
            />
          </g>
        );
      })}

      {/* Engraved North Compass Indicator */}
      <g
        transform={`translate(${minX + 12 * compassScale}, ${minZ + 13 * compassScale}) scale(${compassScale})`}
      >
        <circle
          r={6.4}
          className="fill-[#F6F5F0]/85 stroke-[#8C7137] dark:fill-[#0B120F]/90 dark:stroke-[#D4AF5F]"
          strokeWidth={0.55}
        />
        <polygon points="0,-4.5 2.7,3.1 0,1.4 -2.7,3.1" fill="#D4AF5F" />
        <text
          y={-7.8}
          textAnchor="middle"
          className="fill-[#13261C] dark:fill-[#E5C77B]"
          fontSize={3.6}
          fontWeight={700}
        >
          N
        </text>
      </g>
    </svg>
  );
}
