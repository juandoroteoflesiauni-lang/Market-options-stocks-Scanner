import type { Metadata } from "next";
import { TopNavigationBar } from "@/components/navigation/TopNavigationBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Deep Funnel Station",
  description: "Asymmetric data funnel for quantitative trading",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body>
        <div className="relative flex min-h-screen flex-col">
          <TopNavigationBar />
          <main className="flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
