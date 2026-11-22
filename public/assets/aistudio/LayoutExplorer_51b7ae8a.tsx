"use client";

import { useEffect, useRef, useState } from "react";
import { FACING_LABEL, STATUS_META, summarize, type ProjectLayout } from "@/lib/projects";
import LeadCapture from "./LeadCapture";
import LayoutMap2D from "./LayoutMap2D";
import SceneCanvas, { type SceneHandle } from "./SceneCanvas";

type Props = {
  projectSlug: string;
  layout: ProjectLayout;
};

const chapters = [
  {
    label: "APPROACH",
    title: "Fly in over the roads.",
    copy: "The road widths on this sheet come from the plan itself — approach roads, internal streets and the frontage that sets each plot's address.",
  },
  {
    label: "READ THE SHEET",
    title: "Colour is status.",
    copy: "Sand-green plots are shown available, amber are reserved and clay ones are sold or marked taken on the source sheet. The arrow on each plot shows which way it faces.",
  },
  {
    label: "CHOOSE",
    title: "Click any plot.",
    copy: "Select a plot in 3D or on the map below to see its number, facing, road frontage and area — then ask for that status in writing before you decide.",
  },
];

export default function LayoutExplorer({ projectSlug, layout }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [chapter, setChapter] = useState(0);
  const sectionRef = useRef<HTMLDivElement | null>(null);
  const progressRef = useRef(0.12);
  const sceneRef = useRef<SceneHandle | null>(null);
  const stats = summarize(layout);
  const selectedPlot = selected ? layout.plots.find((p) => p.no === selected) : undefined;

  useEffect(() => {
    let ticking = false;
    const read = () => {
      ticking = false;
      const section = sectionRef.current;
      if (!section) return;
      const rect = section.getBoundingClientRect();
      const travel = Math.max(section.offsetHeight - window.innerHeight, 1);
      const progress = Math.max(0, Math.min(1, -rect.top / travel));
      progressRef.current = progress;
      const nextChapter = Math.min(2, Math.floor(progress * 3));
      setChapter((current) => (current === nextChapter ? current : nextChapter));
    };
    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(read);
      }
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  const active = chapters[chapter];

  return (
    <section className="explorer" id="layout" aria-labelledby="layout-title">
      <div className="explorer__head">
        <div>
          <p className="eyebrow">INTERACTIVE LAYOUT · SCROLL, DRAG, CLICK</p>
          <h2 id="layout-title">The sheet, in your hands.<br /><em>Every plot. Every direction.</em></h2>
        </div>
        <div className="explorer__legend" aria-label="Plot status legend">
          {(Object.keys(STATUS_META) as Array<keyof typeof STATUS_META>).map((key) => (
            <span key={key} className="legend-chip">
              <i style={{ background: STATUS_META[key].color }} />
              {STATUS_META[key].label}
            </span>
          ))}
          <span className="legend-chip legend-chip--arrow"><i className="legend-arrow">▲</i> facing</span>
        </div>
      </div>

      <div className="explorer-scene" ref={sectionRef}>
        <div className="explorer-scene__pin">
          <SceneCanvas
            ref={sceneRef}
            layout={layout}
            selected={selected}
            onSelect={setSelected}
            getProgress={() => progressRef.current}
          />
          <div className="explorer-scene__wash" aria-hidden="true" />
          <div className="explorer-scene__topline">
            <span className="scene-kicker"><span className="scene-kicker__dot" /> 3D LAYOUT RENDER · SCHEMATIC</span>
            <span className="scene-counter">SCROLL TO FLY · DRAG TO ORBIT 360°</span>
          </div>
          <div className="explorer-scene__story" aria-live="polite" aria-atomic="true">
            <p className="scroll-scene__chapter">0{chapter + 1} <span>/</span> {active.label}</p>
            <h3>{active.title}</h3>
            <p>{active.copy}</p>
            <div className="scroll-scene__progress" aria-label={`Step ${chapter + 1} of 3`}>
              {chapters.map((item, index) => (
                <span key={item.label} className={index <= chapter ? "is-active" : ""} />
              ))}
            </div>
          </div>
          <div className="explorer-scene__controls">
            <button type="button" onClick={() => sceneRef.current?.zoomBy(0.85)} aria-label="Zoom in">+</button>
            <button type="button" onClick={() => sceneRef.current?.zoomBy(1.18)} aria-label="Zoom out">−</button>
            <button type="button" onClick={() => sceneRef.current?.resetView()}>Reset view</button>
          </div>
          <p className="scroll-scene__disclaimer">Click a plot to inspect it · rendering simplified from the supplied sheet</p>
        </div>
      </div>

      <div className="explorer-map">
        <div className="explorer-map__board">
          <div className="explorer-map__board-head">
            <div>
              <h3>Plot-by-plot map</h3>
              <p>{stats.total} plots on this sheet · north is up · arrows show the facing of each plot's frontage</p>
            </div>
            <div className="explorer-map__counts">
              <span className="count-chip count-chip--available">{stats.available} available*</span>
              <span className="count-chip count-chip--reserved">{stats.reserved} reserved*</span>
              <span className="count-chip count-chip--sold">{stats.sold} sold / marked</span>
            </div>
          </div>
          <LayoutMap2D layout={layout} selected={selected} onSelect={setSelected} />
        </div>

        <aside className="plot-panel" aria-live="polite" aria-label="Selected plot details">
          {selectedPlot ? (
            <>
              <div className="plot-panel__top">
                <div>
                  <p className="eyebrow">Plot {selectedPlot.no}</p>
                  <h3>Plot {selectedPlot.no}</h3>
                </div>
                <span
                  className={`status-chip status-chip--${selectedPlot.status}`}
                  style={{ background: STATUS_META[selectedPlot.status].color, color: "#14261d" }}
                >
                  {STATUS_META[selectedPlot.status].label}
                </span>
              </div>
              <dl className="plot-panel__grid">
                <div>
                  <dt>Facing</dt>
                  <dd>
                    <span className={`facing-arrow facing-arrow--${selectedPlot.faces}`} aria-hidden="true" />
                    {FACING_LABEL[selectedPlot.faces]}
                    {selectedPlot.road ? ` · fronts ${selectedPlot.road}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Size on sheet</dt>
                  <dd>{selectedPlot.w} × {selectedPlot.d} m</dd>
                </div>
                <div>
                  <dt>Area</dt>
                  <dd>{selectedPlot.areaSqFt ? `≈ ${Math.round(selectedPlot.areaSqFt).toLocaleString("en-IN")} sq ft` : "Confirm per plot"}</dd>
                </div>
                <div>
                  <dt>Status source</dt>
                  <dd>{selectedPlot.status === "sold" ? "Highlighted on the supplied sheet" : selectedPlot.status === "reserved" ? "Pen-circled on the supplied sheet" : "Unmarked on the supplied sheet"}</dd>
                </div>
              </dl>
              <p className="plot-panel__note">Statuses follow the markings on the shared sheet. Get a dated, written confirmation for this exact plot before any payment.</p>
              <div className="plot-panel__actions">
                <LeadCapture
                  label={`Check plot ${selectedPlot.no}`}
                  interest="plot-enquiry"
                  project={projectSlug}
                  plotNumber={selectedPlot.no}
                  className="button button--dark"
                />
                <LeadCapture
                  label="Book a site visit"
                  interest="site-visit"
                  project={projectSlug}
                  className="button button--outline"
                />
              </div>
            </>
          ) : (
            <>
              <p className="eyebrow">Nothing selected yet</p>
              <h3>Pick a plot.</h3>
              <p className="plot-panel__note">
                Click any plot in the 3D view above or on the map beside this panel. You'll see its number, facing direction, fronting road and indicative area — then you can ask for written confirmation.
              </p>
              <div className="plot-panel__actions">
                <LeadCapture label="Book a site visit" interest="site-visit" project={projectSlug} className="button button--dark" />
                <a className="text-link text-link--dark" href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">
                  Open the buyer checklist <span aria-hidden="true">↗</span>
                </a>
              </div>
            </>
          )}
        </aside>
      </div>

      <p className="explorer__disclaimer">
        *Statuses are read from the markings on the supplied layout sheet (highlighted = sold/marked taken, pen-circled = reserved, unmarked = available). This interactive board is a simplified rendering for understanding — not a live register and not the sanctioned plan. Always verify plot number, boundaries, area, availability and approvals against original documents.
      </p>
    </section>
  );
}
