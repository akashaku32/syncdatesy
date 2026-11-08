import { Linter } from "eslint";
import { describe, expect, it } from "vitest";

describe("no-alert", () => {
  const linter = new Linter({ configType: "flat" });

  const verify = (code: string) =>
    linter.verify(
      code,
      [
        {
          files: ["**/*.{js,ts,tsx}"],
          languageOptions: {
            ecmaVersion: 2020,
            sourceType: "module",
            globals: { window: "readonly" },
          },
          rules: { "no-alert": "error" },
        },
      ],
      "feature.tsx",
    );

  it("reports confirm, alert, and prompt", () => {
    const messages = verify(
      "window.confirm('Discard?');\nwindow.alert('Saved');\nprompt('Name');",
    );
    expect(messages.map((message) => message.ruleId)).toEqual([
      "no-alert",
      "no-alert",
      "no-alert",
    ]);
  });

  it("allows an imported confirm helper", () => {
    const messages = verify(
      "import { confirm } from './confirm';\nconst ok = confirm({ title: 'Keep editing' });",
    );
    expect(messages.filter((message) => message.ruleId === "no-alert")).toEqual(
      [],
    );
  });
});
