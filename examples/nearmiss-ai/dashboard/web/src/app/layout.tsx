import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NearMiss AI | Aviation Safety Intelligence",
  description: "Explore real NASA ASRS reports and inspect collaborative investigation traces.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
