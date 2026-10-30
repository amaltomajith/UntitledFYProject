import type { Metadata } from "next";
import { Ide } from "@/components/ide/Ide";

export const metadata: Metadata = { title: "IDE · Puppeteer-Code" };

export default function IdePage() {
  return <Ide />;
}
