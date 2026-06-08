import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Prometheus SSH Migration GUI",
  description: "Generalized SSH controller for Prometheus raw block and Grafana annotation migration"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
