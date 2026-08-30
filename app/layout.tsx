import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CAJNMNSTR — SPY Options Agent",
  description: "The CAJNMNSTR evidence-governed SPY options paper-trading command dashboard.",
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
