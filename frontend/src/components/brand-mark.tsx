import { cn } from "@/lib/utils";

/** The DecisionLedger checkmark logo — the same mark used in the dashboard header. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "grid place-items-center rounded-xl bg-primary text-primary-foreground shadow-[0_0_20px_-4px_var(--brand-glow)]",
        className,
      )}
    >
      <svg
        viewBox="0 0 24 24"
        className="h-1/2 w-1/2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M4 12l5 5L20 6" />
      </svg>
    </div>
  );
}
