import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans_KR } from "next/font/google";
import { SiteHeader } from "@/components/SiteHeader";
import "./globals.css";

const sans = IBM_Plex_Sans_KR({ weight: ["400", "500", "600"], preload: false, variable: "--font-sans" });
const mono = IBM_Plex_Mono({ weight: ["400", "500"], subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "arXiv GraphRAG",
  description: "arXiv 논문 인용 그래프에 묻고, 답의 근거와 경로를 함께 봅니다.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <SiteHeader />
        {children}
      </body>
    </html>
  );
}
