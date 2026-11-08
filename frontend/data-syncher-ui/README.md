# Syncdatesy UI (`data-syncher-ui`)

Customer-facing **React + Vite** SPA for the Syncdatesy platform. API calls go through [`src/queryOptions/`](src/queryOptions/) and [`src/lib/axios/`](src/lib/axios/) only—see [`AGENTS.md`](AGENTS.md).

## Quick start

```powershell
cd frontend/data-syncher-ui
npm ci
npm run dev
```

| Command | Purpose |
|---------|---------|
| `npm run lint` | ESLint |
| `npm run build` | Production build (`dist/`) |
| `npm run audit:deps` | Dependency audit |

Batch/GCP build uses Vite mode `batch` (see repo [`frontend/build-batch-image.sh`](../build-batch-image.sh)).

## Branding & theme

| Item | Location |
|------|----------|
| Product name, tagline, defaults | [`src/constants/branding.ts`](src/constants/branding.ts) |
| Logo + title (`AppBrand`) | [`src/components/branding/AppBrand.tsx`](src/components/branding/AppBrand.tsx) |
| Chakra colors (teal / sky palette) | [`src/theme/theme.ts`](src/theme/theme.ts) |
| Favicon | [`public/logo.svg`](public/logo.svg) |

**Palette (default):** teal `brand.500` (`#14b8a6`), accent sky `#0ea5e9`, sidebar slate `#0f172a`. Auth banner uses a teal→sky gradient.

To rebrand: edit `branding.ts`, replace SVG assets under `src/assets/`, then adjust `theme.ts` tokens.

## Environment

Copy adapter env patterns from the monorepo root [README](../../README.md#restore-secrets-locally). UI batch builds expect API host via Vite env (see `.env.example` when present / `vite` mode `batch`).

## Related docs

- Monorepo: [../../README.md](../../README.md)
- Backend: [../../syncdatesy-adapter/](../../syncdatesy-adapter/)
- Security (frontend): [../../docs/security/frontend.md](../../docs/security/frontend.md)
