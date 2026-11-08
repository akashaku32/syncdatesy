/**
 * @vitest-environment jsdom
 */
import { ChakraProvider } from "@chakra-ui/react";

import { afterEach, describe, expect, it, vi } from "vitest";

import theme from "@/theme/theme";

import ExpandableCodeEditor from "./ExpandableCodeEditor";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

const script = "import pandas as pd\n\ndef transform(df):\n    return df\n";

describe("ExpandableCodeEditor", () => {
  it("puts Save and validate on the editor header and lists the failing line", () => {
    const onValidate = vi.fn();
    render(
      <ChakraProvider value={theme}>
        <ExpandableCodeEditor
          value={script}
          fileName="0fi_gl_4.py"
          onChange={() => undefined}
          onValidate={onValidate}
          validateLabel="Save and validate"
          compact
          diagnostics={[
            {
              line: 4,
              message: "Root function must be named transform (found foo).",
              detail: "Traceback (most recent call last):\nKeyError: 'AMT'",
              source: "server",
            },
          ]}
        />
      </ChakraProvider>,
    );

    const validate = screen.getByRole("button", { name: "Save and validate" });
    expect(validate).toBeTruthy();
    fireEvent.click(validate);
    expect(onValidate).toHaveBeenCalledOnce();

    const problem = screen.getByRole("button", {
      name: /Root function must be named transform/,
    });
    expect(problem.textContent).toContain("Ln 4");
    expect(problem.textContent).toContain("KeyError: 'AMT'");
    fireEvent.click(problem);
  });
});
