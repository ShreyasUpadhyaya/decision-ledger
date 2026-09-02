import { useEffect, useState } from "react";

/**
 * An animated radial gauge. `value` is normalised 0..1; the arc sweeps from empty to
 * `value` on mount (and whenever value/label change). Colour is any CSS colour string,
 * so callers pass a verdict token like `var(--clear)`.
 */
export function RadialGauge({
  value,
  display,
  sublabel,
  color,
  size = 168,
  stroke = 12,
}: {
  value: number;
  display: string;
  sublabel?: string;
  color: string;
  size?: number;
  stroke?: number;
}) {
  const clamped = Math.max(0, Math.min(1, value));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const gapFraction = 0.28; // leave a bottom gap so it reads as a gauge, not a ring
  const arc = circumference * (1 - gapFraction);
  const rotation = 90 + gapFraction * 180;

  const [progress, setProgress] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setProgress(clamped));
    return () => cancelAnimationFrame(id);
  }, [clamped]);

  const offset = arc * (1 - progress);

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90 overflow-visible" style={{ transform: `rotate(${rotation}deg)` }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--border)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${arc} ${circumference}`}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${arc} ${circumference}`}
          strokeDashoffset={offset}
          style={{
            transition: "stroke-dashoffset 1.1s cubic-bezier(0.22, 1, 0.36, 1)",
            filter: `drop-shadow(0 0 8px ${color})`,
          }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          <div className="text-3xl font-bold tabular-nums leading-none" style={{ color }}>
            {display}
          </div>
          {sublabel && <div className="mt-1 text-[0.7rem] uppercase tracking-widest text-muted-foreground">{sublabel}</div>}
        </div>
      </div>
    </div>
  );
}
