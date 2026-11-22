import type { Metadata } from "next";
import Image from "next/image";
import { notFound } from "next/navigation";
import { getProject, projects, summarize } from "@/lib/projects";
import AssistantWidget from "@/app/components/AssistantWidget";
import LayoutExplorer from "@/app/components/LayoutExplorer";
import LeadCapture from "@/app/components/LeadCapture";
import { SiteFooter, SiteHeader } from "@/app/components/SiteChrome";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://samruddhi-estate.example";

type Params = { params: Promise<{ slug: string }> };

export function generateStaticParams() {
  return projects.map((project) => ({ slug: project.slug }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) return { title: "Project not found" };
  return {
    title: `${project.name} — plots, layout map & facing | ${project.district}, Nagpur`,
    description: `${project.name} (${project.location}): ${project.tagline}. Explore the interactive layout sheet — sold, reserved and available plots with facing directions — and verify everything before you buy.`,
    alternates: { canonical: `/projects/${slug}` },
    openGraph: {
      title: `${project.name} — interactive plot layout`,
      description: project.intro.slice(0, 160),
      images: ["/images/butibori-estate.jpg"],
    },
  };
}

export default async function ProjectPage({ params }: Params) {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) notFound();

  const stats = summarize(project.layout);

  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebPage",
        "@id": `${siteUrl}/projects/${slug}/#webpage`,
        name: `${project.name} — interactive layout`,
        url: `${siteUrl}/projects/${slug}`,
        description: project.intro,
        inLanguage: "en-IN",
      },
      {
        "@type": "Place",
        name: project.location,
        address: {
          "@type": "PostalAddress",
          addressLocality: project.location,
          addressRegion: project.state,
          addressCountry: "IN",
        },
      },
      {
        "@type": "FAQPage",
        mainEntity: project.faqs.map((item) => ({
          "@type": "Question",
          name: item.question,
          acceptedAnswer: { "@type": "Answer", text: item.answer },
        })),
      },
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />
      <SiteHeader project={project.slug} />
      <main id="main">
        <section className="project-hero" aria-labelledby="project-title">
          <Image
            className="hero__image"
            src="/images/butibori-estate.jpg"
            alt="Illustrative aerial concept of a plotted neighbourhood near Nagpur, not an on-site photograph"
            fill
            priority
            sizes="100vw"
            quality={88}
          />
          <div className="hero__scrim" aria-hidden="true" />
          <div className="hero__inner">
            <div className="hero__main">
              <p className="hero__location"><span className="hero__location-dot" /> {project.location.toUpperCase()} · {project.state.toUpperCase()}</p>
              <h1 id="project-title">{project.headline}</h1>
              <p className="hero__lede">{project.intro}</p>
              <div className="hero__actions">
                <a className="button button--lime" href="#layout">Open the interactive layout <span aria-hidden="true">↓</span></a>
                <LeadCapture label="Arrange a site visit" interest="site-visit" project={project.slug} className="button button--glass" />
              </div>
              <div className="project-hero__stats">
                <span><b>{stats.total}</b> plots on sheet</span>
                <span><b className="t-avail">{stats.available}</b> available*</span>
                <span><b className="t-res">{stats.reserved}</b> reserved*</span>
                <span><b className="t-sold">{stats.sold}</b> sold / marked</span>
                <span><b>{project.frontage.split("·")[0].trim()}</b> frontage</span>
              </div>
            </div>
            <div className="hero__bottomline">
              <span>{project.name.toUpperCase()} · SEPARATE SHEET</span>
              <span className="hero__scroll">SCROLL TO THE 3D LAYOUT <b aria-hidden="true">↓</b></span>
            </div>
          </div>
        </section>

        <LayoutExplorer projectSlug={project.slug} layout={project.layout} />

        <section className="project-section section-pad" id="facts" aria-labelledby="facts-title">
          <div className="section-heading section-heading--project">
            <div>
              <p className="eyebrow">SHEET FACTS · WITH THEIR SOURCE</p>
              <h2 id="facts-title" className="section-title">What the sheet<br /><em>actually says.</em></h2>
            </div>
            <p className="section-lede">Numbers below are quoted from the supplied sheet or brochure. Treat them as starting points, not guarantees.</p>
          </div>
          <div className="project-facts">
            {project.facts.map((fact, index) => (
              <article className={`project-fact${index === 0 ? " project-fact--big" : ""}`} key={fact.label}>
                <span className="project-fact__index">{fact.label.toUpperCase()}</span>
                <p className="project-fact__number project-fact__number--text">{fact.value}</p>
                {fact.note ? <p>{fact.note}</p> : null}
              </article>
            ))}
            <article className="project-fact project-fact--feature">
              <span className="project-fact__index">DESCRIBED ON MATERIAL</span>
              <div className="feature-pills">{project.claims.map((claim) => <span key={claim}>{claim}</span>)}</div>
              <h3>Promotional claims*</h3>
              <p>*Described in supplied material — inspect on site and verify against approved plans.</p>
            </article>
          </div>
          {project.khNo || project.mouza ? (
            <p className="project-section__source">
              Survey reference: {project.khNo ? `KH No. ${project.khNo}` : ""}{project.mouza ? ` · Mouza ${project.mouza}` : ""} · {project.district} district, {project.state}.
            </p>
          ) : null}
        </section>

        {project.landmarks ? (
          <section className="location-section section-pad" id="location" aria-labelledby="location-title">
            <div className="location-section__content">
              <p className="eyebrow eyebrow--light">LOCAL CONTEXT · NOT A TRAVEL-TIME CLAIM</p>
              <h2 id="location-title" className="section-title" style={{ color: "#f4f2e8" }}>Around Butibori,<br /><em>as named on the reference map.</em></h2>
              <p className="location-section__intro">The reference map shared for {project.name} names the following around the site. It is schematic and not to scale.</p>
              <div className="location-landmarks">
                {project.landmarks.map((landmark, index) => (
                  <div key={landmark.name}>
                    <span>0{index + 1}</span>
                    <p><strong>{landmark.name}</strong><small>{landmark.note}</small></p>
                  </div>
                ))}
              </div>
              <p className="location-section__warning"><span aria-hidden="true">!</span> The locator is illustrative and not to scale. It does not confirm a road, station, distance, travel time or public-project completion date. Check current information with the relevant authority.</p>
            </div>
            <div className="regional-map" role="img" aria-label="Illustrative regional locator between Butibori MIDC and the wider Nagpur, MIHAN and airport corridor">
              <div className="regional-map__top"><span>REGIONAL LOCATOR</span><span>SCHEMATIC / NOT TO SCALE</span></div>
              <svg className="regional-map__drawing" viewBox="0 0 640 480" fill="none" aria-hidden="true">
                <path className="map-road map-road--main" d="M-20 330C108 323 164 291 246 285c109-8 159 35 245 14 75-18 102-91 164-91" />
                <path className="map-road map-road--secondary" d="M85-35c41 100 80 143 159 210 78 66 146 95 199 148 42 43 66 88 93 180" />
                <path className="map-road map-road--fine" d="M-20 125h710M40 400c92-94 174-145 288-199 112-53 202-85 323-93" />
                <path className="map-route" d="M118 274c83 16 120 24 195-23 71-45 113-57 192-71" />
                <circle className="map-halo" cx="118" cy="274" r="24" />
                <circle className="map-dot map-dot--estate" cx="118" cy="274" r="7" />
                <circle className="map-halo" cx="312" cy="251" r="17" />
                <circle className="map-dot" cx="312" cy="251" r="5" />
                <circle className="map-halo" cx="506" cy="180" r="17" />
                <circle className="map-dot" cx="506" cy="180" r="5" />
                <path className="map-park" d="M365 78h91v42h-91z" />
                <path className="map-park" d="M224 360h85v35h-85z" />
                <text x="82" y="237">BUTIBORI</text>
                <text x="265" y="224">MIDC ZONE</text>
                <text x="458" y="151">NAGPUR / MIHAN</text>
                <text className="map-text-small" x="55" y="314">SITE AREA · APPROX.</text>
                <text className="map-text-small" x="361" y="67">OPEN LAND</text>
                <text className="map-text-small" x="224" y="417">REGIONAL ROAD NETWORK</text>
              </svg>
              <div className="regional-map__bottom"><span><i /> Site area · approximate reference</span><span>North is illustrative</span></div>
            </div>
          </section>
        ) : null}

        {project.video ? (
          <section className="site-footage-section section-pad" aria-labelledby="footage-title">
            <div className="site-footage-section__text">
              <p className="eyebrow">THE PLACE, IN MOTION</p>
              <h2 id="footage-title" className="section-title">A picture helps.<br /><em>A visit tells you more.</em></h2>
              <p className="section-lede">Watch a short property walk-through for {project.name}, then see the sheet and the ground with your own eyes. Video can show a moment; only a current visit and document check can confirm a plot.</p>
              <LeadCapture label="Arrange a site visit" interest="site-visit" project={project.slug} className="button button--dark" />
              <p className="footage-note">Clip supplied for this preview · muted by default · no contact details are added by this website</p>
            </div>
            <figure className="property-video">
              <div className="property-video__frame">
                <video controls playsInline muted preload="none" poster="/images/butibori-estate.jpg" aria-label={`Muted property walk-through video for ${project.name}, supplied for this preview`}>
                  <source src={project.video} type="video/mp4" />
                  Your browser does not support this video. Use the site-visit request to see the property in person.
                </video>
                <span className="property-video__label"><i /> PROPERTY WALK-THROUGH</span>
              </div>
              <figcaption>Promotional footage is illustrative. Confirm current plot boundaries, road work, services and availability on site.</figcaption>
            </figure>
          </section>
        ) : null}

        <section className="faq-section section-pad" id="questions" aria-labelledby="faq-title">
          <div className="faq-section__heading">
            <p className="eyebrow">{project.name.toUpperCase()} · STRAIGHT ANSWERS</p>
            <h2 id="faq-title" className="section-title">About this<br /><em>specific sheet.</em></h2>
            <p className="section-lede">Plain answers for {project.name}. If we don’t know, we say so.</p>
            <LeadCapture label="Ask a person" interest="callback" project={project.slug} className="text-link text-link--dark" />
          </div>
          <div className="faq-list">
            {project.faqs.map((item, index) => (
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
              <p className="eyebrow eyebrow--light">{project.name.toUpperCase()}</p>
              <h2>See the sheet.<br /><em>Then see the ground.</em></h2>
            </div>
            <div className="closing-section__action">
              <p>Pick a plot on the map above, note its number and facing, and ask for written confirmation — then visit.</p>
              <LeadCapture label="Request a site visit" interest="site-visit" project={project.slug} className="button button--lime" />
              <a className="closing-section__guide" href="/">Browse other projects <span aria-hidden="true">↗</span></a>
            </div>
          </div>
        </section>
      </main>

      <SiteFooter project={project.slug} />

      <div className="mobile-dock">
        <span>Ask about {project.name}</span>
        <LeadCapture label="Check a plot" interest="plot-enquiry" project={project.slug} className="button button--lime" />
      </div>
      <AssistantWidget project={project.slug} />
    </>
  );
}
