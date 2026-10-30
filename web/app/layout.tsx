import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const sans = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Puppeteer-Code",
  description:
    "A coding assistant that plugs into yours over MCP. Send it your failing tests, get back a diff that passes, and keep your tokens.",
};

export const viewport: Viewport = {
  themeColor: "#07061a",
  colorScheme: "dark",
};

// Runs before first paint: enables the motion layout only when the user has not asked for reduced motion.
const motionScript = `try{if(window.matchMedia('(prefers-reduced-motion: no-preference)').matches)document.documentElement.classList.add('motion-ok')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: motionScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
