import type { Metadata } from "next";
import {
  Inter,
  JetBrains_Mono,
  IBM_Plex_Sans,
  Space_Grotesk,
} from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-ui",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

const ibmPlexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  variable: "--font-heading",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  weight: ["500", "700"],
});

export const metadata: Metadata = {
  title: "Deep Funnel Station - GOKU",
  description: "Asymmetric data funnel for quantitative trading",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} ${ibmPlexSans.variable} ${spaceGrotesk.variable} dark`}
    >
      <body className="antialiased min-h-screen relative overflow-hidden bg-bg-base text-text-primary">
        {/* Noise overlay */}
        <div className="pointer-events-none fixed inset-0 z-40 opacity-[0.015] mix-blend-overlay bg-[url('/noise.png')] bg-repeat" />

        {children}
      </body>
    </html>
  );
}
