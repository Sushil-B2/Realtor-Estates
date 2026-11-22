"use client";

import { FACING_LABEL, STATUS_META, type PlotCell, type ProjectLayout } from "@/lib/projects";

type Props = {
  layout: ProjectLayout;
  selected: string | null;
  onSelect?: (no: string | null) => void;
  interactive?: boolean;
};

function arrowPoints(p: PlotCell): string {
  const s = Math.min(Math.min(p.w, p.d) * 0.22, 2.6);
  const x = p.x;
  const z = p.z;
  switch (p.faces) {
    case "N":
      return `${x},${z - p.d / 2 - s} ${x - s},${z - p.d / 2 + s * 0.7} ${x + s},${z - p.d / 2 + s * 0.7}`;
    case "S":
      return `${x},${z + p.d / 2 + s} ${x - s},${z + p.d / 2 - s * 0.7} ${x + s},${z + p.d / 2 - s * 0.7}`;
    case "E":
      return `${x + p.w / 2 + s},${z} ${x + p.w / 2 - s * 0.7},${z - s} ${x + p.w / 2 - s * 0.7},${z + s}`;
    case "W":
      return `${x - p.w / 2 - s},${z} ${x - p.w / 2 + s * 0.7},${z - s} ${x - p.w / 2 + s * 0.7},${z + s}`;
  }
}

export default function LayoutMap2D({ layout, selected, onSelect, interactive = true }: Props) {
  const { bounds, plots, roads, zones } = layout;
  const minX = bounds.x - bounds.w / 2;
  const minZ = bounds.z - bounds.d / 2;

  return (
    <svg
      className="layout-map"
      viewBox={`${minX} ${minZ} ${bounds.w} ${bounds.d}`}
      role={interactive ? "group" : "img"}
      aria-label={`Layout map with ${plots.length} plots. Green-sand available, amber reserved, clay sold or marked taken. North is up.`}
      preserveAspectRatio="xMidYMid meet"
    >
      <rect x={minX} y={minZ} width={bounds.w} height={bounds.d} className="lm-ground" />

      {zones.map((z, i) => (
        <g key={`zone-${i}`}>
          <rect
            x={z.x - z.w / 2}
            y={z.z - z.d / 2}
            width={z.w}
            height={z.d}
            className={`lm-zone lm-zone--${z.kind}`}
          />
          {z.w > 14 ? (
            <text x={z.x} y={z.z} className="lm-zone-label" textAnchor="middle" dominantBaseline="middle">
              {z.label}
            </text>
          ) : null}
        </g>
      ))}

      {roads.map((r, i) => (
        <g key={`road-${i}`}>
          <rect x={r.x - r.w / 2} y={r.z - r.d / 2} width={r.w} height={r.d} className="lm-road" />
          <text
            x={r.x}
            y={r.z}
            className="lm-road-label"
            textAnchor="middle"
            dominantBaseline="middle"
            transform={r.orient === "v" ? `rotate(-90 ${r.x} ${r.z})` : undefined}
          >
            {r.label}
          </text>
        </g>
      ))}

      {plots.map((p) => {
        const isSelected = selected === p.no;
        const meta = STATUS_META[p.status];
        return (
          <g
            key={p.no}
            className={`lm-plot lm-plot--${p.status}${isSelected ? " is-selected" : ""}`}
            role={interactive ? "button" : undefined}
            tabIndex={interactive ? 0 : undefined}
            aria-label={`Plot ${p.no}, ${meta.label}, faces ${FACING_LABEL[p.faces]}${p.road ? `, fronts ${p.road}` : ""}${p.areaSqFt ? `, about ${Math.round(p.areaSqFt)} sq ft` : ""}`}
            aria-pressed={interactive ? isSelected : undefined}
            onClick={interactive ? () => onSelect?.(isSelected ? null : p.no) : undefined}
            onKeyDown={
              interactive
                ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect?.(isSelected ? null : p.no);
                    }
                  }
                : undefined
            }
          >
            <rect
              x={p.x - p.w / 2}
              y={p.z - p.d / 2}
              width={p.w}
              height={p.d}
              rx={0.7}
              fill={meta.color}
              className="lm-plot-rect"
            />
            <text
              x={p.x}
              y={p.z}
              className="lm-plot-no"
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={Math.max(2.4, Math.min(Math.min(p.w, p.d) * 0.32, 6))}
            >
              {p.no}
            </text>
            <polygon points={arrowPoints(p)} className="lm-arrow" />
          </g>
        );
      })}

      <g className="lm-north" transform={`translate(${minX + 9}, ${minZ + 9})`}>
        <circle r={6} className="lm-north-ring" />
        <polygon points="0,-4.6 3,3.4 0,1.6 -3,3.4" className="lm-north-arrow" />
        <text y={-7.4} textAnchor="middle" className="lm-north-label">N</text>
      </g>
    </svg>
  );
}
