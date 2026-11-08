import prettierConfig from "eslint-config-prettier";
import checkFile from "eslint-plugin-check-file";
import prettierPlugin from "eslint-plugin-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import security from "eslint-plugin-security";
import globals from "globals";
import tseslint from "typescript-eslint";

import prettierRules from "./.prettierrc.cjs";
import js from "@eslint/js";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      prettier: prettierPlugin,
      "check-file": checkFile,
      security,
    },
    rules: {
      "@typescript-eslint/no-unused-vars": "off",
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "prefer-const": "error",
      eqeqeq: ["error", "always"],
      "no-unused-expressions": "error",
      "no-duplicate-imports": "error",
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      "no-console": ["error", { allow: ["warn", "error"] }],
      "security/detect-object-injection": "warn",
      "security/detect-non-literal-regexp": "warn",
      "no-trailing-spaces": "error",
      "no-multiple-empty-lines": "error",
      "prettier/prettier": ["error", prettierRules],
      "check-file/filename-naming-convention": [
        "error",
        {
          "src/*.{tsx}": "PASCAL_CASE",
          "src/*.{js,ts}": "KEBAB_CASE",
        },
        {
          ignoreMiddleExtensions: true,
        },
      ],
    },
  },
  {
    files: ["src/components/**/*.{ts,tsx}"],
    rules: {
      // warn until legacy axios usage in components is migrated to queryOptions/
      "no-restricted-imports": [
        "warn",
        {
          paths: [
            {
              name: "axios",
              message:
                "Import HTTP only via queryOptions/ hooks; lib/axios is for the API layer.",
              allowTypeImports: true,
            },
          ],
          patterns: [
            {
              group: ["@/lib/axios", "@/lib/axios/**"],
              message:
                "Do not import from @/lib/axios in components; use queryOptions/ hooks.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "src/components/dashboard/components/Connectors/components/ConnectorDetails/components/Tabs/Schema/TransformModal/**/*.{ts,tsx}",
      "src/components/ui/ConfirmDialog.tsx",
      "src/components/ui/confirm.tsx",
    ],
    rules: {
      "no-alert": "error",
    },
  },
  prettierConfig,
);
