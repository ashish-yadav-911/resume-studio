import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Resume Studio",
  description: "AI-powered resume optimization",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen text-slate-800">
        <header className="border-b border-indigo-100 bg-white/70 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <div>
              <h1 className="text-xl font-bold bg-gradient-to-r from-indigo-600 to-cyan-500 bg-clip-text text-transparent">
                Resume Studio
              </h1>
              <p className="text-xs text-slate-500">Conversational, evidence-grounded resume optimization</p>
            </div>
            <span className="chip bg-indigo-50 text-indigo-700">Live workspace</span>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-5">{children}</main>
      </body>
    </html>
  );
}