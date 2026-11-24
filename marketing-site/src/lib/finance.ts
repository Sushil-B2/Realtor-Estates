export type FinanceInputs = {
  areaSqFt: number;
  ratePerSqFt: number;
  plcPercent: number;
  downPaymentPercent: number;
  annualInterestRate: number;
  tenureYears: number;
  womenBuyerConcession: boolean;
};

export type FinanceBreakdown = {
  baseCost: number;
  plcAmount: number;
  agreementValue: number;
  stampDutyRate: number;
  stampDuty: number;
  registrationFee: number;
  totalAcquisitionCost: number;
  downPaymentAmount: number;
  loanPrincipal: number;
  monthlyEmi: number;
  totalInterest: number;
  totalLoanPayable: number;
};

export function calculatePlotFinance(inputs: FinanceInputs): FinanceBreakdown {
  const area = Math.max(200, inputs.areaSqFt);
  const rate = Math.max(500, inputs.ratePerSqFt);
  const plcPct = Math.max(0, Math.min(20, inputs.plcPercent));

  const baseCost = Math.round(area * rate);
  const plcAmount = Math.round(baseCost * (plcPct / 100));
  const agreementValue = baseCost + plcAmount;

  // Maharashtra (Nagpur) typical stamp duty reference: 6% standard, 5% women homebuyer concession
  const stampDutyRate = inputs.womenBuyerConcession ? 5 : 6;
  const stampDuty = Math.round(agreementValue * (stampDutyRate / 100));
  // Maharashtra registration fee: 1% capped at ₹30,000
  const registrationFee = Math.min(30000, Math.round(agreementValue * 0.01));

  const totalAcquisitionCost = agreementValue + stampDuty + registrationFee;

  const dpPct = Math.max(10, Math.min(90, inputs.downPaymentPercent));
  const downPaymentAmount = Math.round(totalAcquisitionCost * (dpPct / 100));
  const loanPrincipal = Math.max(0, totalAcquisitionCost - downPaymentAmount);

  const months = Math.max(12, Math.round(inputs.tenureYears * 12));
  const monthlyRate = inputs.annualInterestRate / 12 / 100;

  let monthlyEmi = 0;
  if (loanPrincipal > 0) {
    if (monthlyRate <= 0) {
      monthlyEmi = Math.round(loanPrincipal / months);
    } else {
      const factor = Math.pow(1 + monthlyRate, months);
      monthlyEmi = Math.round((loanPrincipal * monthlyRate * factor) / (factor - 1));
    }
  }

  const totalLoanPayable = monthlyEmi * months;
  const totalInterest = Math.max(0, totalLoanPayable - loanPrincipal);

  return {
    baseCost,
    plcAmount,
    agreementValue,
    stampDutyRate,
    stampDuty,
    registrationFee,
    totalAcquisitionCost,
    downPaymentAmount,
    loanPrincipal,
    monthlyEmi,
    totalInterest,
    totalLoanPayable,
  };
}

export function formatINR(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Math.round(amount));
}

export function formatINRCompact(amount: number): string {
  if (amount >= 10_000_000) {
    return `₹${(amount / 10_000_000).toFixed(2)} Cr`;
  }
  if (amount >= 100_000) {
    return `₹${(amount / 100_000).toFixed(2)} L`;
  }
  return formatINR(amount);
}
