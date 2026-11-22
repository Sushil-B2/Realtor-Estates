import LeadCapture from "./LeadCapture";

export function SiteHeader({ project = "samruddhi-estate" }: { project?: string }) {
  return (
    <>
      <div className="announcement-bar">
        <div className="announcement-bar__inner">
          <span><i /> NAGPUR, MAHARASHTRA · PLOTTED DEVELOPMENTS</span>
          <span className="announcement-bar__right">Every project on its own sheet <b>↗</b></span>
        </div>
      </div>
      <header className="site-header">
        <div className="site-header__inner">
          <a className="brand" href="/" aria-label="Back to all projects">
            <span className="brand__mark" aria-hidden="true">
              <svg viewBox="0 0 40 40" fill="none">
                <path d="M5 21 20 8l15 13M10 18v16h20V18M16 34V22h8v12" />
                <path d="M4 35h32M3 13l5-5M32 8l5 5" />
              </svg>
            </span>
            <span className="brand__name"><strong>Estate</strong><small>PORTFOLIO · NAGPUR</small></span>
          </a>
          <nav className="desktop-nav" aria-label="Main navigation">
            <a href="/#projects">Projects</a>
            <a href="/#walkthrough">3D tour</a>
            <a href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">Buyer guide</a>
            <a href="#questions">FAQs</a>
          </nav>
          <div className="site-header__actions">
            <a className="header-link" href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">The buyer guide <span aria-hidden="true">↓</span></a>
            <LeadCapture label="Book a visit" interest="site-visit" project={project} className="button button--header" />
          </div>
          <details className="mobile-menu">
            <summary aria-label="Open navigation menu"><span /><span /></summary>
            <nav aria-label="Mobile navigation">
              <a href="/#projects">Projects</a>
              <a href="/#walkthrough">3D tour</a>
              <a href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">Buyer guide</a>
              <a href="#questions">FAQs</a>
            </nav>
          </details>
        </div>
      </header>
    </>
  );
}

export function SiteFooter({ project = "samruddhi-estate" }: { project?: string }) {
  return (
    <footer className="site-footer">
      <div className="site-footer__main">
        <a className="brand brand--footer" href="/" aria-label="Back to all projects">
          <span className="brand__mark" aria-hidden="true">
            <svg viewBox="0 0 40 40" fill="none"><path d="M5 21 20 8l15 13M10 18v16h20V18M16 34V22h8v12M4 35h32M3 13l5-5M32 8l5 5" /></svg>
          </span>
          <span className="brand__name"><strong>Estate</strong><small>PORTFOLIO · NAGPUR</small></span>
        </a>
        <p>Residential plots around Nagpur.<br />Each project mapped honestly.</p>
        <nav aria-label="Footer navigation">
          <a href="/#projects">Projects</a>
          <a href="/#walkthrough">3D tour</a>
          <a href="/plot-buyer-checklist.html" target="_blank" rel="noreferrer">Buyer checklist</a>
          <a href="#questions">FAQs</a>
        </nav>
        <LeadCapture label="Request a call back" interest="callback" project={project} className="button button--footer" />
      </div>
      <div className="site-footer__legal">
        <p>Project facts, road widths, amenity areas and plot statuses are read from promotional sheets shared for this website preview and are not independently verified. Interactive boards are simplified renderings, not live inventory. No price, title, approval, registration, completion date, utility service, public-project status, access, investment return or availability is guaranteed. Verify original documents, current approvals and site conditions with competent authorities and an independent local lawyer before making a decision or payment.</p>
        <div><span>© {new Date().getFullYear()} Estate Portfolio · Preview website</span><span>Mapped honestly. Verified carefully.</span></div>
      </div>
    </footer>
  );
}
