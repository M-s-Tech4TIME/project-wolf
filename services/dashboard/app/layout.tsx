import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AuthProvider } from "@/components/auth-provider";
// Slice 5.0c-i.5: light-mode highlight.js theme. Dark-mode overrides
// live in globals.css under `.dark .hljs-*` selectors (this project
// uses class-based dark mode via Tailwind v4's @custom-variant, so
// the theme can't be selected via prefers-color-scheme alone).
import "highlight.js/styles/github.css";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Wolf",
  description: "Agentic AI for Wazuh security analysts",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
