import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/brand-mark";

export function WelcomePage() {
  return (
    <div className="grid min-h-svh place-items-center cockpit-bg px-6">
      <div className="animate-fade-up relative z-10 max-w-xl text-center">
        <BrandMark className="mx-auto h-16 w-16" />

        <h1 className="mt-6 text-5xl font-bold leading-none tracking-tight md:text-6xl">
          <span className="brand-gradient-text">DecisionLedger</span>
        </h1>
        <p className="mt-2 text-sm font-medium uppercase tracking-[0.2em] text-muted-foreground">
          Decision Automation Cockpit · DTDL
        </p>

        <p className="mx-auto mt-6 max-w-md text-balance text-base text-muted-foreground">
          Turn every order into an explainable, real-time credit &amp; risk verdict — approve,
          refer, or decline — backed by deterministic rules and an AI explanation layer.
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Button asChild size="lg" className="px-6">
            <Link to="/signin">Sign In</Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="px-6">
            <Link to="/signup">Sign Up</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
