import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/Providers";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Repo Healer - AI-Powered Code Health Dashboard",
  description:
    "Analyze repository code health, identify risky files, and auto-heal with AI-driven refactoring.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.className} bg-gray-950 text-gray-100 min-h-screen antialiased`}
      >
        <Providers>
          <nav className="border-b border-gray-800/60 backdrop-blur-xl bg-gray-950/80 sticky top-0 z-50">
            <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
              <a href="/" className="flex items-center gap-2.5">
                <span className="text-2xl">🔬</span>
                <span className="text-lg font-bold bg-gradient-to-r from-emerald-400 to-cyan-400 bg-clip-text text-transparent">
                  Repo Healer
                </span>
              </a>
              <div className="flex items-center gap-6">
                <a
                  href="/"
                  className="text-sm text-gray-400 hover:text-white transition-colors"
                >
                  Dashboard
                </a>
                <a
                  href="/heatmap"
                  className="text-sm text-gray-400 hover:text-white transition-colors"
                >
                  Heatmap
                </a>
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
