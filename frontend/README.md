# DecisionLedger — Decision Cockpit

A React 19 + Tailwind v4 + [shadcn/ui](https://ui.shadcn.com) frontend for the
DecisionLedger decision automation platform, styled as a Deutsche Telekom–magenta "mission-control"
cockpit. Two views:

- **Decision Cockpit** — an animated verdict theatre (composite verdict + radial risk/
  confidence gauges), an **AI explanation** panel (streams the backend's natural-language
  rationale, badged `LLM` or `Deterministic`), the per-domain decision set, a visual
  **4-phase rule-trace pipeline** (GATE → SCORING → TERMS → OVERLAY), signal health, and a
  live failure-injection panel for fail-closed demos.
- **Rule Studio** — showcases the backend's LLM/RAG layer: turn plain-English policy into a
  validated JSON rule (`POST /v1/rules/generate`) and semantically search the active
  ruleset (`GET /v1/rules/search`).

Capability pills in the header reflect `/ready` (API online, AI mode, RAG backend). A
light/dark theme toggle is included; dark is the default hero surface.

This is a companion to the backend (rule engine + API). Point it at wherever that API is
running via `VITE_API_BASE_URL` (defaults to `http://127.0.0.1:8000`).

## Run it

The backend must be running and reachable (CORS is configured there for
`localhost:5173`).

```bash
npm install
npm run dev
```

Open http://localhost:5173. The header shows a green/red dot for backend reachability;
if it's red, start the backend API first and reload.

To point at a different backend URL:

```bash
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

## Layout

```
src/
├── index.css              # DT-magenta design system: theme tokens, gauges, animations
├── lib/
│   ├── types.ts           # mirrors the backend request/response + LLM/RAG shapes
│   ├── api.ts             # fetch wrappers for every backend route
│   ├── decision.ts         # shared verdict → severity → colour mapping
│   └── scenarios.ts       # canned requests, one per worked example in the backend docs
└── components/dashboard/
    ├── decision-hero.tsx       # animated composite-verdict theatre
    ├── risk-gauge.tsx          # animated radial gauge
    ├── ai-explanation-panel.tsx# streams the backend's NL explanation
    ├── trace-pipeline.tsx      # visual 4-phase GATE→SCORING→TERMS→OVERLAY trace
    ├── rule-studio.tsx         # NL→rule generator + semantic rule search
    ├── decision-card.tsx, signal-health-panel.tsx, scenario-picker.tsx,
    └── failure-injection-panel.tsx, recent-decisions.tsx, theme-toggle.tsx
```

## Build

```bash
npm run build
```

Type-checks and produces a production bundle in `dist/`.
