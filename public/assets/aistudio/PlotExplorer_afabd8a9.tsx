"use client";

import { useMemo, useState } from "react";
import LeadCapture from "./LeadCapture";

type LotFilter = "all" | "available" | "marked";

const markedSoldLots = new Set([
  2, 3, 6, 7, 8, 10, 12, 14, 16, 18, 19, 21, 23, 25, 27, 29, 31, 33, 34, 36,
  38, 40, 42, 44, 46, 49, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72,
]);

const lots = Array.from({ length: 72 }, (_, index) => ({
  number: index + 1,
  markedSold: markedSoldLots.has(index + 1),
}));

const filters: Array<{ value: LotFilter; label: string }> = [
  { value: "all", label: "All plots" },
  { value: "available", label: "Shown as available" },
  { value: "marked", label: "Marked sold" },
];

export default function PlotExplorer() {
  const [filter, setFilter] = useState<LotFilter>("all");
  const [selected, setSelected] = useState<number | null>(null);
  const visibleLots = useMemo(
    () => lots.filter((lot) => filter === "all" || (filter === "marked" ? lot.markedSold : !lot.markedSold)),
    [filter],
  );
  const selectedLot = selected === null ? null : lots.find((lot) => lot.number === selected) ?? null;
  const availableCount = lots.filter((lot) => !lot.markedSold).length;
  const markedCount = lots.length - availableCount;

  return (
    <div className="plot-explorer">
      <div className="plot-explorer__toolbar">
        <div className="plot-filter" role="group" aria-label="Filter plot board">
          {filters.map((item) => (
            <button
              key={item.value}
              type="button"
              className={filter === item.value ? "plot-filter__button is-active" : "plot-filter__button"}
              aria-pressed={filter === item.value}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="plot-explorer__counts" aria-live="polite">
          <span>{availableCount} sample open</span><i /> <span>{markedCount} marked on plan</span>
        </p>
      </div>

      <div className="plot-map" aria-label="Schematic interactive plot board">
        <div className="plot-map__road plot-map__road--top"><span>12 m approach road · plan reference</span></div>
        <div className="plot-map__outer">
          <div className="plot-map__compass" aria-hidden="true"><span>N</span><i>✦</i></div>
          <div className="plot-map__lots" role="group" aria-label="Select a plot number">
            {visibleLots.map((lot) => {
              const markerClass = lot.markedSold
                ? lot.number % 5 === 0
                  ? "plot-cell--circled"
                  : lot.number % 2 === 0
                    ? "plot-cell--orange"
                    : "plot-cell--yellow"
                : "";
              const selectedClass = selected === lot.number ? " is-selected" : "";
              return (
                <button
                  key={lot.number}
                  type="button"
                  className={`plot-cell${lot.markedSold ? " plot-cell--sold" : " plot-cell--available"}${markerClass ? ` ${markerClass}` : ""}${selectedClass}`}
                  aria-pressed={selected === lot.number}
                  aria-label={`Plot ${String(lot.number).padStart(2, "0")}, ${lot.markedSold ? "marked sold in the sample plan" : "shown as sample availability"}`}
                  onClick={() => setSelected(lot.number)}
                >
                  <span>{String(lot.number).padStart(2, "0")}</span>
                </button>
              );
            })}
          </div>
          <div className="plot-map__amenity">
            <span className="plot-map__amenity-icon" aria-hidden="true">✳</span>
            <span>OPEN SPACE</span>
            <small>Reference-plan amenity zone</small>
          </div>
          <div className="plot-map__road plot-map__road--middle"><span>9 m internal road · plan reference</span></div>
          <div className="plot-map__bottom-lots" aria-hidden="true">
            <span>01</span><span>02</span><span>03</span><span>04</span><span>05</span><span>06</span><span>07</span><span>08</span>
          </div>
        </div>
        <div className="plot-map__road plot-map__road--bottom"><span>Walk the layout before you decide</span></div>
      </div>

      <div className="plot-legend" aria-label="Plot status legend">
        <span><i className="legend-dot legend-dot--open" /> Shown as available · confirm live</span>
        <span><i className="legend-dot legend-dot--yellow" /> Highlighted / circled in source plans · marked sold</span>
      </div>

      <div className="plot-explorer__notice">
        <span className="notice-mark" aria-hidden="true">i</span>
        <p><strong>Important:</strong> Yellow, orange and pen-circled plots in the supplied photographs are treated as sold, following the source legend. This interactive 72-lot board is a schematic sample—not a transcription of every plan sheet and not a live register. Ask for a dated, written availability check for the exact plot before paying.</p>
      </div>

      {selectedLot ? (
        <div className="selected-plot" aria-live="polite">
          <div>
            <p className="eyebrow">Plot board selection</p>
            <h3>Plot {String(selectedLot.number).padStart(2, "0")}</h3>
            <p>
              {selectedLot.markedSold
                ? "This sample lot is marked sold in the supplied-plan legend. Ask the team to confirm whether that status has changed."
                : "This sample lot is shown as available on the demonstration board. Current availability, area and price must be confirmed."}
            </p>
          </div>
          <LeadCapture
            label={selectedLot.markedSold ? "Ask about this status" : "Check this plot"}
            interest="plot-enquiry"
            plotNumber={String(selectedLot.number).padStart(2, "0")}
            className="button button--dark"
          />
        </div>
      ) : null}
    </div>
  );
}
