import { ChapterIndicator } from "@/components/ChapterIndicator";
import { Nav } from "@/components/Nav";
import { ScrollDirector } from "@/components/ScrollDirector";
import { SceneMount } from "@/components/scene/SceneMount";
import { About } from "@/components/sections/About";
import { Capabilities } from "@/components/sections/Capabilities";
import { Footer } from "@/components/sections/Footer";
import { GetStarted } from "@/components/sections/GetStarted";
import { Hero } from "@/components/sections/Hero";
import { HowItWorks } from "@/components/sections/HowItWorks";
import { Mcp } from "@/components/sections/Mcp";

export default function Page() {
  return (
    <>
      <div className="cone" aria-hidden="true" />
      <SceneMount />
      <Nav />
      <main className="main">
        <Hero />
        <About />
        <Mcp />
        <HowItWorks />
        <Capabilities />
        <GetStarted />
      </main>
      <Footer />
      <ChapterIndicator />
      <ScrollDirector />
    </>
  );
}
