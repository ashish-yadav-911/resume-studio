import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Resume Studio",
  description: "AI-powered resume optimization",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-black">
        <header className="border-b border-black px-6 py-4">
          <h1 className="text-xl font-bold">Resume Studio</h1>
          <p className="text-sm">Black & white resume optimization workspace</p>
        </header>
        <main className="px-4 py-4">{children}</main>
      </body>
    </html>
  );
}