import type { Metadata } from "next";
import { Crimson_Text, Space_Grotesk } from "next/font/google";

import { TopNav } from "@/components/top-nav";
import "./globals.css";

const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-sans" });
const serif = Crimson_Text({ subsets: ["latin"], variable: "--font-serif", weight: ["400", "600"] });

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
