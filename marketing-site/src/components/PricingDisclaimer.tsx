import { AlertCircle } from "lucide-react";

type DisclaimerProps = {
  variant?: "default" | "compact" | "inline";
  surface?: "dark" | "auto";
  className?: string;
};

/**
 * Reusable business & customer safety pricing disclaimer placed wherever
 * indicative rates, plot budgets, stamp duty, or EMI estimates are displayed.
 */
export function PricingDisclaimer({
  variant = "default",
  surface = "auto",
  className = "",
}: DisclaimerProps) {
  const darkClasses =
    surface === "dark"
      ? "border-[#D4AF5F]/25 bg-[#0a130f]/90 text-[#B8C5BC]"
      : "border-black/10 bg-[#F3F1E8] text-[#4C5B52] dark:border-[#D4AF5F]/20 dark:bg-[#0D1612] dark:text-[#A5B4A9]";

  const titleClasses =
    surface === "dark"
      ? "text-[#E5C77B]"
      : "text-[#7A5E24] dark:text-[#E5C77B]";

  if (variant === "inline") {
    return (
      <p
        className={`flex items-start gap-1.5 text-[11px] leading-relaxed ${
          surface === "dark"
            ? "text-[#A7B6AC]"
            : "text-[#58675E] dark:text-[#96A79B]"
        } ${className}`}
      >
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
        <span>
          <strong className={`font-semibold ${titleClasses}`}>Pricing Disclaimer:</strong> All
          displayed per-sq.ft rates, base values, and EMI figures are indicative estimates for
          preliminary planning only — excluding applicable PLC, Maharashtra stamp duty,
          registration, development charges, and taxes, and are subject to change without prior
          notice. Always verify the dated official written cost sheet before booking.
        </span>
      </p>
    );
  }

  return (
    <div
      role="note"
      aria-label="Pricing and Loan Calculation Disclaimer"
      className={`rounded-xl border px-3.5 py-3 text-[11px] leading-relaxed ${darkClasses} ${className}`}
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#D4AF5F]" aria-hidden="true" />
        <p>
          <strong className={`font-semibold ${titleClasses}`}>
            Pricing &amp; Estimate Disclaimer (Customer &amp; Business Protection):
          </strong>{" "}
          {variant === "compact" ? (
            <>
              All calculated plot prices, PLC, Maharashtra stamp duty, registration fees, and
              monthly loan EMIs are indicative approximations for budgeting only and do not
              constitute a binding price offer, allotment guarantee, or bank loan sanction. Actual
              payable figures depend on prevailing Ready Reckoner norms, statutory taxes, bank
              eligibility, and the signed official cost sheet issued at booking.
            </>
          ) : (
            <>
              Every price per sq.ft, total land budget, Preferential Location Charge (PLC),
              Maharashtra stamp duty, registration charge, and monthly bank EMI shown by this
              calculator is an illustrative estimate meant solely for preliminary financial planning.
              Rates and availability are subject to revision without prior notice and do not create
              any contractual offer, legal commitment, or bank loan approval. Final payable amounts
              are governed strictly by prevailing Maharashtra government statutory rates, bank
              sanction terms, and the dated official written cost sheet executed at the time of
              booking.
            </>
          )}
        </p>
      </div>
    </div>
  );
}

/**
 * Globally recognized WhatsApp brand glyph SVG for intuitive mobile & desktop messaging cues.
 */
export function WhatsAppIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <path d="M12.03 2C6.51 2 2.02 6.48 2.02 12c0 1.77.46 3.5 1.34 5.02L2 22l5.12-1.34A9.96 9.96 0 0 0 12.03 22C17.55 22 22.04 17.52 22.04 12S17.55 2 12.03 2Zm0 18.18c-1.5 0-2.97-.4-4.26-1.17l-.31-.18-3.04.8.81-2.96-.2-.31A8.16 8.16 0 0 1 3.84 12c0-4.51 3.68-8.18 8.19-8.18 4.51 0 8.19 3.67 8.19 8.18s-3.68 8.18-8.19 8.18Zm4.49-6.13c-.25-.12-1.46-.72-1.69-.8-.22-.08-.39-.12-.55.12-.16.25-.63.8-.78.96-.14.16-.29.18-.53.06-.25-.12-1.04-.38-1.98-1.22-.73-.65-1.23-1.46-1.37-1.71-.14-.25-.02-.38.11-.5.11-.11.25-.29.37-.43.12-.14.16-.25.25-.41.08-.16.04-.31-.02-.43-.06-.12-.55-1.33-.76-1.82-.2-.48-.4-.41-.55-.42h-.47c-.16 0-.43.06-.66.31-.22.25-.86.84-.86 2.05s.88 2.38 1 2.54c.12.16 1.73 2.65 4.2 3.71.59.25 1.05.41 1.4.52.59.19 1.13.16 1.55.1.47-.07 1.46-.6 1.67-1.17.2-.57.2-1.07.14-1.17-.06-.1-.22-.16-.47-.29Z" />
    </svg>
  );
}
