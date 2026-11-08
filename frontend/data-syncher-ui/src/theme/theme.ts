import {
  createSystem,
  defaultConfig,
  defineConfig,
  defineRecipe,
  mergeConfigs,
} from "@chakra-ui/react";

import "@fontsource/plus-jakarta-sans/400.css";
import "@fontsource/plus-jakarta-sans/600.css";

const theme = defineConfig({
  globalCss: {
    ":root": {
      "--brand-500": "{colors.brand.500}",
      "--brand-600": "{colors.brand.600}",
    },
    ".stripe-field .StripeElement, .stripe-field .__PrivateStripeElement": {
      width: "100%",
      minHeight: "24px",
      flex: "1 1 auto",
      display: "block",
    },
    ".stripe-field .StripeElement--focus": {
      outline: "none",
    },
    ".stripe-field .StripeElement--invalid": {
      color: "#e53e3e",
    },
    ".stripe-field iframe": {
      width: "100% !important",
      minHeight: "24px",
      display: "block",
    },
    body: {
      bg: { _light: "bg.canvas", _dark: "gray.900" },
      color: { _light: "gray.800", _dark: "white" },
      fontFamily: "'Plus Jakarta Sans', sans-serif",
    },
    "[id]": {
      scrollMarginTop: "5.5rem",
    },
  },
  cssVarsPrefix: "ck",
  theme: {
    breakpoints: {
      sm: "320px",
      md: "768px",
      lg: "960px",
      xl: "1200px",
    },
    tokens: {
      colors: {
        brand: {
          50: { value: "#f0fdfa" },
          100: { value: "#ccfbf1" },
          200: { value: "#99f6e4" },
          300: { value: "#5eead4" },
          400: { value: "#2dd4bf" },
          500: { value: "#14b8a6" },
          600: { value: "#0d9488" },
          700: { value: "#0f766e" },
          800: { value: "#115e59" },
          900: { value: "#134e4a" },
        },
        accent: {
          50: { value: "#f0f9ff" },
          100: { value: "#e0f2fe" },
          200: { value: "#bae6fd" },
          300: { value: "#7dd3fc" },
          400: { value: "#38bdf8" },
          500: { value: "#0ea5e9" },
          600: { value: "#0284c7" },
          700: { value: "#0369a1" },
          800: { value: "#075985" },
          900: { value: "#0c4a6e" },
        },
        navy: {
          50: { value: "#f4f7fb" },
          100: { value: "#e8eef6" },
          800: { value: "#1e2a44" },
          900: { value: "#111c33" },
          950: { value: "#0b1324" },
        },
      },
      fonts: {
        heading: { value: "'Plus Jakarta Sans', sans-serif" },
        body: { value: "'Plus Jakarta Sans', sans-serif" },
      },
      radii: {
        card: { value: "1rem" },
      },
    },
    semanticTokens: {
      colors: {
        "checkbox-border": {
          value: { _light: "gray.200", _dark: "gray.800" },
        },
        bg: {
          canvas: { value: "#f2f6f5" },
          surface: { value: "#ffffff" },
          subtle: { value: "{colors.brand.50}" },
          auth: {
            value:
              "linear-gradient(180deg, #f8fbfa 0%, #f0f5f4 45%, #eef4f8 100%)",
          },
        },
        border: {
          subtle: { value: "{colors.gray.100}" },
        },
        fg: {
          muted: { value: "{colors.gray.500}" },
        },
        sidebar: {
          bg: { value: "{colors.navy.950}" },
          fg: { value: "#e2e8f0" },
          hover: { value: "{colors.navy.800}" },
          activeBg: { value: "rgba(20, 184, 166, 0.15)" },
          activeFg: { value: "{colors.brand.300}" },
          border: { value: "rgba(45, 212, 191, 0.35)" },
        },
        gradient: {
          hero: {
            value:
              "linear-gradient(165deg, #0b1324 0%, #0f3d3a 28%, #0d9488 58%, #115e59 100%)",
          },
          cta: {
            value:
              "linear-gradient(to right, {colors.brand.600}, {colors.accent.600})",
          },
        },
        brand: {
          solid: { value: "{colors.brand.600}" },
          contrast: { value: "{colors.white}" },
          fg: { value: "{colors.brand.700}" },
          muted: { value: "{colors.brand.100}" },
          subtle: { value: "{colors.brand.200}" },
          emphasized: { value: "{colors.brand.400}" },
          focusRing: { value: "{colors.brand.500}" },
          accent: { value: "{colors.accent.500}" },
          accentOrange: { value: "#f59e0b" },
          accentSky: { value: "{colors.accent.500}" },
        },
      },
    },
    recipes: {
      button: defineRecipe({
        base: {
          borderRadius: "lg",
          fontWeight: "semibold",
        },
      }),
      input: defineRecipe({
        base: {
          borderRadius: "lg",
        },
      }),
      card: defineRecipe({
        base: {
          borderRadius: "card",
          borderWidth: "1px",
          borderColor: "border.subtle",
          boxShadow: "sm",
        },
      }),
    },
  },
});

const config = mergeConfigs(defaultConfig, theme);
export default createSystem(config);
