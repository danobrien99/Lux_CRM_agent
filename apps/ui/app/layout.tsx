import type { Metadata } from "next";
import { Montserrat, Playfair_Display } from "next/font/google";

import { TopNav } from "@/components/top-nav";
import "./globals.css";

const sans = Montserrat({ subsets: ["latin"], variable: "--font-sans", weight: ["300", "400", "500"] });
const serif = Playfair_Display({ subsets: ["latin"], variable: "--font-serif", weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: "Lux CRM Agent",
  description: "Relationship intelligence CRM augmentation",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${serif.variable}`}>
        <div className="bgPattern" />
        <TopNav />
        <main className="pageContainer">{children}</main>
      </body>
    </html>
  );
}
