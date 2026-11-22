"use client";

import { useEffect, useId, useState, type FormEvent } from "react";

type LeadInterest = "general" | "site-visit" | "plot-enquiry" | "callback" | "brochure-request";

type LeadCaptureProps = {
  label: string;
  interest?: LeadInterest;
  project?: string;
  plotNumber?: string;
  className?: string;
};

type FormStatus = { kind: "success" | "error"; message: string } | null;

function getCalendarUrl(value: string | undefined) {
  if (!value) return "";
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return "";
    url.searchParams.set("embed", "true");
    return url.toString();
  } catch {
    return "";
  }
}

export default function LeadCapture({
  label,
  interest = "general",
  project = "samruddhi-estate",
  plotNumber,
  className = "button button--primary",
}: LeadCaptureProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const titleId = useId();
  const descriptionId = useId();
  const calendarUrl = interest === "site-visit"
    ? getCalendarUrl(process.env.NEXT_PUBLIC_CALCOM_LINK)
    : "";

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = {
      fullName: values.get("fullName"),
      phone: values.get("phone"),
      email: values.get("email"),
      interest,
      project,
      plotNumber: plotNumber || "",
      message: values.get("message"),
      consent: values.get("consent") === "on",
      website: values.get("website"),
    };

    try {
      const response = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(result.error || "Please review your details and try again.");
      setStatus({ kind: "success", message: result.message || "Thank you. Your request has been received." });
      form.reset();
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "We couldn't send that request just now.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={className} onClick={() => { setOpen(true); setStatus(null); }}>
        {label}
        <span aria-hidden="true">↗</span>
      </button>
      {open ? (
        <div
          className="modal-scrim"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className={`modal-card${calendarUrl ? " modal-card--calendar" : ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
          >
            <div className="modal-card__header">
              <div>
                <p className="eyebrow">A real conversation, no pressure</p>
                <h2 id={titleId}>{interest === "site-visit" ? "Make time to see it." : "Let’s make the next step clear."}</h2>
                <p id={descriptionId} className="modal-card__intro">
                  {interest === "site-visit"
                    ? "Choose a time if the live calendar is connected, or request a visit and the team will confirm it with you."
                    : "Share only what’s needed for a reply. A person can help confirm details that a plan or page can’t."}
                </p>
              </div>
              <button type="button" className="modal-close" aria-label="Close dialog" onClick={() => setOpen(false)}>×</button>
            </div>

            {calendarUrl ? (
              <div className="cal-embed">
                <iframe
                  src={calendarUrl}
                  title="Book a Samruddhi Estate site visit"
                  loading="lazy"
                  referrerPolicy="strict-origin-when-cross-origin"
                />
                <a href={calendarUrl.replace("&embed=true", "").replace("?embed=true", "")} target="_blank" rel="noreferrer">
                  Open the scheduler in a new tab ↗
                </a>
              </div>
            ) : status?.kind === "success" ? (
              <div className="form-success" role="status">
                <span className="form-success__icon" aria-hidden="true">✓</span>
                <h3>Thank you. We have your request.</h3>
                <p>{status.message}</p>
                <button type="button" className="button button--outline" onClick={() => setOpen(false)}>Done</button>
              </div>
            ) : (
              <form className="lead-form" onSubmit={submit}>
                <input type="hidden" name="interest" value={interest} />
                <input type="hidden" name="project" value={project} />
                {plotNumber ? <input type="hidden" name="plotNumber" value={plotNumber} /> : null}
                <label className="form-field">
                  <span>Your name</span>
                  <input name="fullName" type="text" autoComplete="name" minLength={2} maxLength={120} placeholder="Name" required />
                </label>
                <div className="lead-form__row">
                  <label className="form-field">
                    <span>Phone number</span>
                    <input name="phone" type="tel" inputMode="tel" autoComplete="tel" maxLength={32} placeholder="Include country code if outside India" required />
                  </label>
                  <label className="form-field">
                    <span>Email <em>optional</em></span>
                    <input name="email" type="email" inputMode="email" autoComplete="email" maxLength={180} placeholder="you@example.com" />
                  </label>
                </div>
                <label className="form-field">
                  <span>What would you like to know? <em>optional</em></span>
                  <textarea name="message" rows={3} maxLength={1200} placeholder={plotNumber ? `Please confirm current details for plot ${plotNumber}…` : "Tell us what matters most to you…"} />
                </label>
                <label className="form-honeypot" aria-hidden="true">
                  Leave this field empty
                  <input name="website" tabIndex={-1} autoComplete="off" />
                </label>
                <label className="consent-check">
                  <input type="checkbox" name="consent" required />
                  <span>I agree to be contacted about this request. My details will be used only to respond to this enquiry.</span>
                </label>
                {status?.kind === "error" ? <p className="form-error" role="alert">{status.message}</p> : null}
                <button type="submit" className="button button--primary lead-form__submit" disabled={busy}>
                  {busy ? "Sending…" : interest === "site-visit" ? "Request a site visit" : "Send my enquiry"}
                  <span aria-hidden="true">↗</span>
                </button>
                <p className="form-fineprint">No price, plot status or approval is confirmed until a person verifies it in writing.</p>
              </form>
            )}
          </section>
        </div>
      ) : null}
    </>
  );
}
