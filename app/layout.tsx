import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CAJNMNSTR — Evidence Decides",
  description: "A local concept dashboard for an evidence-governed AI paper-trading agent.",
  icons: {
    icon: "/cajnmstr-icon.png",
    shortcut: "/cajnmstr-icon.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
