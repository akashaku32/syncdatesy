import {
  createSystem,
  defaultConfig,
  defineConfig,
  mergeConfigs,
} from "@chakra-ui/react";

import "@fontsource/plus-jakarta-sans/400.css";
import "@fontsource/plus-jakarta-sans/600.css";

const theme = defineConfig({
  globalCss: {
    ":root": {
      "--brand-500": "{colors.brand.500}",
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
      bg: { _light: "white", _dark: "gray.900" },
      color: { _light: "gray.800", _dark: "white" },
      fontFamily: "'Plus Jakarta Sans', sans-serif",
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
          500: { value: "#0ea5e9" },
          600: { value: "#0284c7" },
        },
        sidebar: {
          bg: { value: "#0f172a" },
          border: { value: "#14b8a6" },
        },
      },
      fonts: {
        heading: { value: "'Plus Jakarta Sans', sans-serif" },
        body: { value: "'Plus Jakarta Sans', sans-serif" },
      },
    },
    semanticTokens: {
      colors: {
        "checkbox-border": {
          value: { _light: "gray.200", _dark: "gray.800" },
        },
        brand: {
          solid: { value: "{colors.brand.500}" },
          contrast: { value: "{colors.white}" },
          fg: { value: "{colors.brand.700}" },
          muted: { value: "{colors.brand.100}" },
          subtle: { value: "{colors.brand.200}" },
          emphasized: { value: "{colors.brand.300}" },
          focusRing: { value: "{colors.brand.500}" },
          accentOrange: { value: "#f59e0b" },
          accentSky: { value: "{colors.accent.500}" },
        },
      },
    },
  },
});

const config = mergeConfigs(defaultConfig, theme);
export default createSystem(config);
