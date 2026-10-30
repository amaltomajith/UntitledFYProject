import type { Metadata } from "next";
import { Benchmarks } from "@/components/app/Benchmarks";

export const metadata: Metadata = { title: "Benchmarks · Puppeteer-Code" };

export default function BenchmarksPage() {
  return <Benchmarks />;
}
