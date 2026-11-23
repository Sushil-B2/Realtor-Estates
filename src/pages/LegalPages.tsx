import { ArrowLeft, ArrowUpRight, FileText, Home, MapPin, ShieldCheck } from "lucide-react";
import { PROJECTS } from "../data/projects";

type LegalPageProps = {
  onNavigate: (path: string) => void;
};

function LegalShell({
  eyebrow,
  title,
  updatedDate,
  icon,
  children,
  onNavigate,
}: {
  eyebrow: string;
  title: string;
  updatedDate: string;
  icon: "privacy" | "terms";
  children: React.ReactNode;
  onNavigate: (path: string) => void;
}) {
  return (
    <section className="min-h-[82svh] bg-[#F6F5F0] py-12 dark:bg-[#080C0A] sm:py-16 lg:py-20">
      <div className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-10">
        {/* Prominent Navigation Breadcrumb */}
        <nav aria-label="Breadcrumb" className="mb-6 flex flex-wrap items-center gap-3 text-xs">
          <a
            href="/"
            onClick={(e) => {
              e.preventDefault();
              onNavigate("/");
            }}
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-lg border border-black/15 bg-white px-3.5 py-2 font-medium text-[#16261E] transition-colors hover:border-[#8C6D2E] dark:border-white/15 dark:bg-[#101814] dark:text-[#E8E4D8] dark:hover:border-[#D4AF5F]"
          >
            <Home className="h-3.5 w-3.5 text-[#8C6D2E] dark:text-[#D4AF5F]" />
            <span>Home</span>
          </a>
          <button
            type="button"
            onClick={() => onNavigate("/")}
            className="inline-flex min-h-[40px] items-center gap-1.5 text-[#516057] hover:text-[#102018] dark:text-[#98A89D] dark:hover:text-[#E5C77B]"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>Back to Nagpur Layouts</span>
          </button>
        </nav>

        <article className="rounded-2xl border border-black/10 bg-white p-6 dark:border-[#D4AF5F]/20 dark:bg-[#101814] sm:p-10">
          <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.14em] text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
            {icon === "privacy" ? (
              <ShieldCheck className="h-4 w-4 shrink-0" />
            ) : (
              <FileText className="h-4 w-4 shrink-0" />
            )}
            <span>{eyebrow}</span>
          </div>
          <h1 className="font-display mt-3 text-4xl font-normal tracking-tight text-[#102018] dark:text-[#F5F2E9] sm:text-5xl">
            {title}
          </h1>
          <p className="mt-2 font-mono-tabular text-xs text-[#64736A] dark:text-[#8E9F93]">
            Realtor Estates — Property in Nagpur · Last Updated: {updatedDate}
          </p>

          <div className="mt-8 space-y-6 border-t border-black/10 pt-8 text-sm leading-relaxed text-[#3D4C43] dark:border-white/10 dark:text-[#B8C4BB]">
            {children}
          </div>
        </article>

        {/* Quick Project Redirect Strip */}
        <div className="mt-10 rounded-2xl border border-black/10 bg-[#ECEAE1] p-6 dark:border-white/10 dark:bg-[#0D1512]">
          <p className="text-xs font-semibold tracking-wider text-[#8C6D2E] uppercase dark:text-[#D4AF5F]">
            Explore Nagpur NMRDA Layouts
          </p>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {PROJECTS.map((project) => (
              <a
                key={project.slug}
                href={`/projects/${project.slug}`}
                onClick={(e) => {
                  e.preventDefault();
                  onNavigate(`/projects/${project.slug}`);
                }}
                className="flex min-h-[48px] items-center justify-between gap-2 rounded-xl border border-black/10 bg-white px-4 py-3 text-xs font-medium text-[#102018] transition-colors hover:border-[#8C6D2E] dark:border-white/10 dark:bg-[#101814] dark:text-[#F4F1E7] dark:hover:border-[#D4AF5F]"
              >
                <span className="inline-flex items-center gap-2">
                  <MapPin className="h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" />
                  <span>{project.name}</span>
                </span>
                <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[#8C6D2E] dark:text-[#D4AF5F]" />
              </a>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export function PrivacyPolicyPage({ onNavigate }: LegalPageProps) {
  return (
    <LegalShell
      eyebrow="Data Privacy & GDPR / DPDP Compliance"
      title="Privacy Policy"
      updatedDate="November 2026"
      icon="privacy"
      onNavigate={onNavigate}
    >
      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          1. Information We Collect
        </h2>
        <p className="mt-2">
          When you request a site visit, written plot availability confirmation, cost sheet, or our
          14-Point Maharashtra Plot Buyer’s Guide on Realtor Estates — Property in Nagpur, we
          collect only the information you voluntarily submit: your full name, phone/WhatsApp
          number, optional email address, shortlisted Nagpur estate corridor, preferred plot number,
          and optional budget or plot-loan preference.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          2. How We Use Your Information
        </h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5">
          <li>
            Deliver dated written plot availability, layout brochures, cost sheets, and the unlocked
            Maharashtra Plot Buyer’s Guide.
          </li>
          <li>
            Coordinate private on-site visits across Salai Dabha, Mouza Kanholi, Hingna, and MIHAN
            corridors in Nagpur.
          </li>
          <li>
            Assist with optional bank plot-loan pre-qualification requests when explicitly selected
            by you.
          </li>
          <li>
            Improve mobile and 3D map usability through anonymized, aggregated interaction insights
            (only when you accept optional analytics preferences).
          </li>
        </ul>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          3. Zero Data Resale &amp; Third-Party Protection
        </h2>
        <p className="mt-2">
          We never sell, rent, or trade your personal phone number, email address, or property
          enquiry records to third-party brokers, telemarketers, or lead aggregators. Stored CRM
          previews mask contact identifiers for security, and personal landholder or surveyor
          details from layout sheets are redacted from public views.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          4. Cookies &amp; Consent Controls (GDPR &amp; India DPDP Act 2023)
        </h2>
        <p className="mt-2">
          We use essential browser storage strictly to remember your display theme (dark/light) and
          cookie preference. You may choose “Essential Only” at any time in the footer notice to
          disable optional engagement analytics without affecting your ability to inspect 3D maps or
          book site visits.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          5. Your Rights &amp; Data Deletion
        </h2>
        <p className="mt-2">
          In accordance with India’s Digital Personal Data Protection (DPDP) Act and applicable GDPR
          principles, you may request access to, correction of, or deletion of your enquiry details,
          or opt out of WhatsApp/email follow-up communications at any time by replying to your
          confirmation thread or requesting removal through our Nagpur advisory desk.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          6. Google Maps Platform Integration
        </h2>
        <p className="mt-2">
          Our optional Satellite &amp; Landmark View integrates Google Maps Platform features. When
          you open the interactive Google Map view, your interaction is also governed by the{" "}
          <a
            href="https://policies.google.com/privacy?utm_campaign=gmp_mcp_codeassist_v1_aistudio"
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-[#8C6D2E] underline underline-offset-2 dark:text-[#E5C77B]"
          >
            Google Privacy Policy
          </a>
          .
        </p>
      </div>
    </LegalShell>
  );
}

export function TermsAndConditionsPage({ onNavigate }: LegalPageProps) {
  return (
    <LegalShell
      eyebrow="Legal Terms & Pricing Safety Notice"
      title="Terms & Conditions"
      updatedDate="November 2026"
      icon="terms"
      onNavigate={onNavigate}
    >
      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          1. Informational Nature of 3D Maps &amp; Digital Layouts
        </h2>
        <p className="mt-2">
          All interactive WebGL 3D layouts, 360-degree plot pop-out models, road width labels,
          compass indicators, and photographic visuals on Realtor Estates — Property in Nagpur are
          illustrative digital aids prepared from reference layout sheets. They do not constitute an
          official government-certified survey, binding offer of sale, or live allotment register.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          2. Pricing, Cost Sheet &amp; Loan EMI Disclaimer
        </h2>
        <p className="mt-2">
          All per-sq.ft starting rates (including Residential and Commercial corridor pricing),
          Preferential Location Charges (PLC), Maharashtra stamp duty percentages, registration
          fees, and monthly bank EMI calculations displayed on this website are strictly indicative
          estimates for preliminary financial planning. Prices, plot availability, and statutory
          government charges are subject to revision without prior notice. Bank loan eligibility,
          sanction amounts, and interest rates are determined solely by the respective lending
          institution. Final binding terms apply exclusively under an officially issued, dated
          written cost sheet and registered Sale Deed / Agreement for Sale.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          3. Independent Buyer Due Diligence (NMRDA, 7/12 &amp; MahaRERA)
        </h2>
        <p className="mt-2">
          Before paying any token amount or entering into a property transaction, buyers are
          strongly advised to independently verify original title deeds, latest 7/12 (Satbara)
          extracts, Non-Agricultural (NA) conversion orders, NMRDA / Town Planning layout sanctions,
          demarcated boundary stones on site, and applicable MahaRERA portal records with an
          independent Maharashtra property advocate.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          4. Plot Availability &amp; Allotment Status
        </h2>
        <p className="mt-2">
          Marked statuses (“Available”, “Reserved”, “Sold Out · Allotted”) reflect periodic sheet
          updates and may change between updates. Submitting an online enquiry or scheduling a site
          visit does not automatically reserve or allot any parcel until written confirmation is
          issued by Realtor Estates.
        </p>
      </div>

      <div>
        <h2 className="font-display text-2xl text-[#102018] dark:text-[#F4F1E7]">
          5. Intellectual Property, Google Maps Terms &amp; Governing Law
        </h2>
        <p className="mt-2">
          The Realtor Estates brand emblem, website code, 3D visual presentations, and editorial
          guides are protected intellectual property and may not be reproduced commercially without
          written permission. Use of the embedded Google Maps Satellite &amp; Landmark features is
          additionally subject to the{" "}
          <a
            href="https://maps.google.com/help/terms_maps/?utm_campaign=gmp_mcp_codeassist_v1_aistudio"
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-[#8C6D2E] underline underline-offset-2 dark:text-[#E5C77B]"
          >
            Google Maps / Google Earth Additional Terms of Service
          </a>
          . Any disputes arising from use of this website shall be governed by the laws of India and
          subject to the exclusive jurisdiction of competent courts in Nagpur, Maharashtra.
        </p>
      </div>
    </LegalShell>
  );
}
