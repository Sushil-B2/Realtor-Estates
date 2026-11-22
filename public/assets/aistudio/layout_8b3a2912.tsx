import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL;
const metadataBase = new URL(siteUrl || "https://samruddhi-estate.example");

export const metadata: Metadata = {
  title: {
    default: "Plotted developments around Nagpur — Samruddhi Estate, Sajal, Sadaj & Ananta & Akasha",
    template: "%s | Estate Portfolio Nagpur",
  },
  description:
    "Explore real layout sheets for residential plot projects around Nagpur, Maharashtra — each project on its own page with an interactive 3D and 2D map showing sold, reserved and available plots and their facing direction. Verify every document before you buy.",
  applicationName: "Estate Portfolio Nagpur",
  keywords: [
    "residential plots in Butibori",
    "plots for sale near Nagpur",
    "Butibori MIDC residential plots",
    "plot layout sheets Nagpur",
    "Dongargaon plots Nagpur",
    "plots near Nagpur with layout plan",
    "plot site visit Nagpur",
  ],
  openGraph: {
    type: "website",
    siteName: "Estate Portfolio Nagpur",
    title: "Plotted developments around Nagpur — mapped honestly",
    description:
      "Interactive 3D and 2D layout sheets for plot projects near Nagpur. Sold, reserved and available plots marked; facing shown per plot.",
    locale: "en_IN",
    images: ["/images/butibori-estate.jpg"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Plotted developments around Nagpur — mapped honestly",
    description:
      "Interactive layout sheets for plot projects near Nagpur: sold / reserved / available plots with facing directions.",
    images: ["/images/butibori-estate.jpg"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
  metadataBase,
  ...(siteUrl ? { alternates: { canonical: "/" } } : {}),
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-IN">
      <body>{children}</body>
    </html>
  );
}
