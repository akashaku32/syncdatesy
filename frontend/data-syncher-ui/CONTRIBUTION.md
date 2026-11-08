## ⚡ Development Guidelines

**Data Syncher UI** is more than a codebase — it’s a living ecosystem.  
Every contribution, commit, and comment adds a note to its ongoing symphony.  
To keep that music clean and resonant, follow these guiding principles.

---

### 🧭 Code Philosophy

- **Clarity over cleverness** — Write code that your future self will thank you for.  
- **Composability first** — Small, reusable, self-contained components form the backbone of the UI.  
- **Consistency is kindness** — Keep patterns predictable; it’s how teams move fast without friction.  
- **Type with care** — TypeScript is not a shield, but a map — use it to navigate confidently.

> “Write code as though someone will read it like poetry —  
> because in the best projects, they always do.”

---

### 🪶 Styling & Theming

- Use **Chakra UI** for all layout and design primitives (`Box`, `Flex`, `Stack`, `Text`, etc.).  
- Global design tokens (colors, typography, spacing) live under `src/theme/`.  
- Avoid inline styles — prefer Chakra props or theme references.  
- Use **SCSS** only for global resets or non-component-specific overrides (found in `main.scss`).

---

### 🌐 Data & React Query

- Define queries and mutations inside `src/queryOptions/`.  
- Each API module should export:
  - A **query key**
  - A **fetch function**
  - A **custom hook** (`useFetchData`, `useUpdateItem`, etc.)  
- Always handle loading, error, and empty states gracefully — the UI should feel *alive*, never frozen.  
- Use `queryClient.invalidateQueries()` wisely to refresh data after successful mutations.

---

### 🧩 Components & Layout

- Reusable, presentational components live under `src/components/`.  
- Route-level pages belong in `src/pages/`.  
- Page scaffolds or wrappers (like dashboards, settings layouts) reside in `src/layouts/`.  
- Shared widgets or cross-page UI elements belong to `src/shared/`.

Each component should:
- Be **functionally pure** (no side effects in render).  
- Receive all dynamic data through **props**.  
- Keep logic in custom hooks where possible.  
- Be written in **TypeScript + JSX**.

---

### 🧠 Naming Conventions

| Type | Convention | Example |
|------|-------------|----------|
| Component | PascalCase | `UserProfileCard` |
| Hook | camelCase + `use` prefix | `useFetchUsers` |
| File / Folder | kebab-case | `user-profile-card.tsx` |
| Type / Interface | PascalCase + `T` or `I` prefix (optional) | `UserProfile`, `IUserResponse` |
| Constants | SCREAMING_SNAKE_CASE | `API_BASE_URL` |
| Functions | camelCase | `fetchUserData()` |

---

### 🧹 Linting & Formatting

- Run **ESLint** before committing — ensures quality and consistency.
- **Prettier** keeps the codebase neat — it formats automatically on save.
- Pre-commit hooks via **Husky** enforce these rules to prevent regressions.
- Avoid disabling lint rules unless absolutely necessary (and document why).

```bash
# Run lint check
npm run lint

# Format all files
npm run format
