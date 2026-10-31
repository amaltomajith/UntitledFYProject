import type { Metadata } from "next";
import { Console } from "@/components/app/Console";

export const metadata: Metadata = { title: "Console · CompactCode" };

export default function ConsolePage() {
  return <Console />;
}
