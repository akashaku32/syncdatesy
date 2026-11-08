module.exports = {
  plugins: ["@trivago/prettier-plugin-sort-imports"],
  semi: true, // Add a semicolon at the end of every statement
  trailingComma: "all", // Add a trailing comma where possible
  printWidth: 80, // Wrap lines at 80 characters
  tabWidth: 2, // Set the number of spaces per indentation level
  useTabs: false, // Use spaces instead of tabs for indentation
  arrowParens: "always", // Always include parentheses around arrow function arguments
  proseWrap: "never", // Do not wrap markdown text
  bracketSpacing: true, // Print spaces between brackets in object literals
  jsxBracketSameLine: false, // Put the closing `>` of a multi-line JSX element at the end of the last line
  endOfLine: "lf", // Use LF for line endings
  importOrder: [
    "^react$",
    "@chakra-ui/react",
    "^react-icons(/.*)?$",
    "<THIRD_PARTY_MODULES>",
    "^@context/(.*)$",
    "^@/(.*)$",
    "^[./].*\\.scss$",
    "[./]",
  ],
  importOrderSeparation: true,
  importOrderSortSpecifiers: true,
};
