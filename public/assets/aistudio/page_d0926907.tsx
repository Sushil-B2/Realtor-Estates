import Image from "next/image";
import { projects, summarize } from "@/lib/projects";
import AssistantWidget from "./components/AssistantWidget";
import ImmersiveJourney from "./components/ImmersiveJourney";
import LayoutMap2D from "./components/LayoutMap2D";
import LeadCapture from "./components/LeadCapture";
import { SiteFooter, SiteHeader } from "./components/SiteChrome";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://samruddhi-estate.example";

const faqs = [
  {
    question: "What projects are listed here?",
    answer:
      "Each project has its own page with its own layout sheet rendered interactively: Samruddhi Estate in Butibori (sheet plots 84–253), Sajal — Sheet 111 (KH No. 106–112, 18 m D.P. road frontage), Sadaj — Sheet 110 (KH No. 107–112, 12 m road frontage, plots 01–76) and Ananta & Akasha in Mouza Dongargaon (plots 1–28, 30 m road frontage).",
  },
  {
    question: "How do I read the plot colours on each map?",
    answer:
      "On the supplied sheets, highlighted (yellow/orange) plots are treated as sold or marked taken, pen-circled plots as reserved, and unmarked plots as available. Every project page shows the same legend, with arrows marking which direction each plot's frontage faces. These boards are renderings, not live inventory — always request a dated written confirmation for an exact plot.",
  },
  {
    question: "Are the road widths and plot areas real?",
    answer:
      "Road widths (9 m / 12 m / 18 m / 30 m), amenity and open-space areas, survey/KH references and example plot areas are taken from the supplied sheets and shown with their source. Exact plot boundaries and current availability must still be verified against the sanctioned plan and sale documents.",
  },
  {
    question: "Are these projects approved or RERA-registered?",
    answer:
      "No approval or registration number has been verified for this preview. Before paying anything, check the sanctioned layout, land title, encumbrances, land use, applicable RERA registration and revenue records with the relevant authority and an independent Maharashtra property lawyer. A free printable checklist is linked below.",
  },
  {
    question: "Are nearby metro, airport or government projects guaranteed?",
    answer:
      "No. Reference maps may name nearby corridors (Butibori MIDC, the interchange, outer ring road, MIHAN, airport corridor), but nothing here confirms a route, distance, travel time, approval or completion date. Verify current status directly with the responsible public authority.",
  },
  {
    question: "How do I arrange a site visit?",
    answer:
      "Open any project page and use the site-visit request. If a Cal.com booking link has been configured, the live scheduler opens; otherwise the team confirms a suitable time after receiving your request.",
  },
];

const structuredData = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${siteUrl}/#organization`,
      name: "Estate Portfolio Nagpur",
      description:
        "Residential plotted-development enquiries around Nagpur, Maharashtra — each project with its own interactive layout sheet.",
      url: siteUrl,
    },
    {
      "@type": "WebSite",
      "@id": `${siteUrl}/#website`,
      name: "Estate Portfolio Nagpur",
      url: siteUrl,
      inLanguage: "en-IN",
      publisher: { "@id": `${siteUrl}/#organization` },
    },
    {
      "@type": "ItemList",
      name: "Plotted development projects around Nagpur",
      itemListElement: projects.map((project, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: project.name,
        url: `${siteUrl}/projects/${project.slug}`,
      })),
    },
    {
      "@type": "FAQPage",
      mainEntity: faqs.map((item) => ({
        "@type": "Question",
        name: item.question,
        acceptedAnswer: { "@type": "Answer", text: item.answer },
      })),
    },
  ],
};

export default function HomePage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />
      <SiteHeader />
      <main id="main">
        <section className="hero" id="home" aria-labelledby="hero-title">
          <Image
            className="hero__image"
            src="/images/butibori-estate.jpg"
            alt="Illustrative aerial concept of a planned plotted neighbourhood near Nagpur, not an on-site photograph"
            fill
            priority
            sizes="100vw"
            quality={88}
          />
          <div className="hero__scrim" aria-hidden="true" />
          <div className="hero__inner">
            <div className="hero__main">
              <p className="hero__location"><span className="hero__location-dot" /> PLOTTED DEVELOPMENTS · NAGPUR DISTRICT</p>
              <h1 id="hero-title">Four sheets.<br /><em>Every plot mapped honestly.</em></h1>
              <p className="hero__lede">Explore real layout sheets from around Nagpur — Butibori and Dongargaon — rendered in 3D and 2D. See which plots are marked sold, reserved or open, and which way each one faces.</p>
              <div className="hero__actions">
                <a className="button button--lime" href="#projects">Explore the projects <span aria-hidden="true">↓</span></a>
                <LeadCapture label="Arrange a site visit" interest="site-visit" className="button button--glass" />
              </div>
              <div className="hero__microproof">
                <span><i>01</i> Separate sheet per project</span>
                <span><i>02</i> Sold / reserved / available marked</span>
                <span><i>03</i> Facing shown per plot</span>
              </div>
            </div>
            <aside className="hero-note" aria-label="How to read the maps">
              <div className="hero-note__top"><span>HOW TO READ</span><span>01 / 03</span></div>
              <p className="hero-note__figure">3<span> states</span></p>
              <p className="hero-note__label">Available · Reserved · Sold-marked</p>
              <div className="hero-note__rule" />
              <div className="hero-note__bottom"><span>From the source markings*</span><span className="hero-note__arrow" aria-hidden="true">↗</span></div>
              <small>*Always confirm live status in writing.</small>
            </aside>
            <div className="hero__bottomline">
              <span>ONE PORTFOLIO · SEPARATE PROJECTS</span>
              <span className="hero__scroll">SCROLL TO EXPLORE <b aria-hidden="true">↓</b></span>
            </div>
          </div>
        </section>

        <section className="projects-section section-pad" id="projects" aria-labelledby="projects-title">
          <div className="decision-section__intro">
            <div>
              <p className="eyebrow">THE PROJECTS · SEPARATE SHEETS</p>
              <h2 id="projects-title" className="section-title">Pick a project.<br /><em>Open its real layout.</em></h2>
            </div>
            <p className="section-lede">No mixing: each development keeps its own page, its own plot numbers and its own markings. The thumbnails below are the actual sheets, miniaturised.</p>
          </div>
          <div className="project-grid">
            {projects.map((project) => {
              const stats = summarize(project.layout);
              return (
                <a className="project-card" key={project.slug} href={`/projects/${project.slug}`}>
                  <div className="project-card__map" aria-hidden="true">
                    <LayoutMap2D layout={project.layout} selected={null} interactive={false} />
                  </div>
                  <div className="project-card__body">
                    <div className="project-card__heading">
                      <h3>{project.name}</h3>
                      <span aria-hidden="true">↗</span>
                    </div>
                    <p className="project-card__loc">{project.nameSub || project.location}</p>
                    <p className="project-card__tag">{project.tagline}</p>
                    <div className="project-card__stats">
                      <span className="count-chip count-chip--available">{stats.available} available*</span>
                      <span className="count-chip count-chip--reserved">{stats.reserved} reserved*</span>
                      <span className="count-chip count-chip--sold">{stats.sold} sold / marked</span>
                    </div>
                    <span className="project-card__cta">Explore layout & 3D view <b aria-hidden="true">→</b></span>
                  </div>
                </a>
              );
            })}
          </div>
          <p className="project-section__source">*Statuses read from markings on the supplied sheets — not live inventory. Open a project for the interactive 3D + 2D map with facing directions.</p>
        </section>

        <ImmersiveJourney />

        <section className="guide-section section-pad" id="buyer-guide" aria-labelledby="guide-title">
          <div className="guide-card">
            <div className="guide-card__copy">
              <p className="eyebrow eyebrow--light">A FREE, PRACTICAL FIELD GUIDE</p>
              <h2 id="guide-title">Before you buy the land,<br /><em>know what to ask.</em></h2>
              <p>Get a printable checklist for title, layout permissions, plot boundaries, facing/frontage, current services, written pricing and the questions worth taking to a local property lawyer — for any of these sheets.</p>
              <a className="button button--lime" href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">Open the buyer checklist <span aria-hidden="true">↗</span></a>
              <small>No gate. No email capture. Print it or save as PDF from your browser.</small>
            </div>
            <div className="guide-card__art" aria-hidden="true">
              <div className="guide-paper">
                <span className="guide-paper__stamp">FIELD NOTES / 01</span>
                <strong>Before<br />you buy<br /><i>the land.</i></strong>
                <span className="guide-paper__rule" />
                <span className="guide-paper__line" /><span className="guide-paper__line" /><span className="guide-paper__line guide-paper__line--short" />
                <span className="guide-paper__check">✓</span>
              </div>
              <span className="guide-orbit guide-orbit--one" /><span className="guide-orbit guide-orbit--two" />
            </div>
          </div>
        </section>

        <section className="faq-section section-pad" id="questions" aria-labelledby="faq-title">
          <div className="faq-section__heading">
            <p className="eyebrow">STRAIGHT ANSWERS · BEFORE THE CALL</p>
            <h2 id="faq-title" className="section-title">Good questions<br /><em>deserve plain answers.</em></h2>
            <p className="section-lede">The important details in one place. If we don’t know, we say so.</p>
            <LeadCapture label="Ask a person" interest="callback" className="text-link text-link--dark" />
          </div>
          <div className="faq-list">
            {faqs.map((item, index) => (
              <details className="faq-item" key={item.question} open={index === 0}>
                <summary><span className="faq-item__number">0{index + 1}</span><span>{item.question}</span><i aria-hidden="true" /></summary>
                <p>{item.answer}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="closing-section">
          <div className="closing-section__inner">
            <div>
              <p className="eyebrow eyebrow--light">THE NEXT STEP IS YOURS</p>
              <h2>Curious is a good<br /><em>place to start.</em></h2>
            </div>
            <div className="closing-section__action">
              <p>Open a project sheet, pick a plot, check its facing — then ask for it in writing and see the ground yourself.</p>
              <LeadCapture label="Request a site visit" interest="site-visit" className="button button--lime" />
              <a className="closing-section__guide" href="/#projects">Or browse all projects <span aria-hidden="true">↗</span></a>
            </div>
          </div>
        </section>
      </main>

      <SiteFooter />

      <div className="mobile-dock">
        <span>Start with a conversation</span>
        <LeadCapture label="Check a plot" interest="plot-enquiry" className="button button--lime" />
      </div>
      <AssistantWidget />
    </>
  );
}
