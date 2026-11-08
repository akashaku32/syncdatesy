/**
 * @vitest-environment jsdom
 */
import { ChakraProvider } from "@chakra-ui/react";

import { afterEach, describe, expect, it, vi } from "vitest";

import theme from "@/theme/theme";

import { ConfirmHost, confirm } from "./confirm";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

afterEach(() => {
  cleanup();
});

const renderHost = () =>
  render(
    <ChakraProvider value={theme}>
      <button type="button">Trigger</button>
      <ConfirmHost />
    </ChakraProvider>,
  );

const discardOptions = {
  title: "Discard unsaved changes?",
  description: "You have unsaved changes to the column mapping for BUKRS.",
  details: ["3 columns added", "1 renamed"],
  confirmLabel: "Discard changes",
  cancelLabel: "Keep editing",
  tone: "danger" as const,
  icon: "warning" as const,
};

describe("ConfirmDialog", () => {
  it("resolves true or false without calling window.confirm", async () => {
    const spy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderHost();

    const accepted = confirm(discardOptions);
    fireEvent.click(
      await screen.findByRole("button", { name: "Discard changes" }),
    );
    await expect(accepted).resolves.toBe(true);
    expect(spy).not.toHaveBeenCalled();

    const declined = confirm(discardOptions);
    fireEvent.click(
      await screen.findByRole("button", { name: "Keep editing" }),
    );
    await expect(declined).resolves.toBe(false);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("focuses the safe button, labels the dialog, and shows change chips", async () => {
    renderHost();
    void confirm(discardOptions);
    const dialog = await screen.findByRole("alertdialog");
    const keep = screen.getByRole("button", { name: "Keep editing" });
    await waitFor(() => expect(document.activeElement).toBe(keep));
    const labelledBy = dialog.getAttribute("aria-labelledby");
    const describedBy = dialog.getAttribute("aria-describedby");
    expect(labelledBy).toBeTruthy();
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(labelledBy || "")?.textContent).toBe(
      "Discard unsaved changes?",
    );
    expect(document.getElementById(describedBy || "")?.textContent).toMatch(
      /column mapping for BUKRS/,
    );
    expect(screen.getByText("3 columns added")).toBeTruthy();
    expect(screen.getByText("1 renamed")).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement || dialog, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.click(keep);
  });

  it("cancels on Escape and the close button", async () => {
    renderHost();
    const escaped = confirm(discardOptions);
    await screen.findByRole("alertdialog");
    fireEvent.keyDown(document.activeElement || document.body, {
      key: "Escape",
    });
    await expect(escaped).resolves.toBe(false);

    const closed = confirm({
      title: "Reset to the uploaded JSON?",
      description:
        "This restores 1 removed columns and discards 0 added columns and 0 renames.",
      confirmLabel: "Reset mapping",
      cancelLabel: "Cancel",
    });
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    await expect(closed).resolves.toBe(false);
  });

  it("ignores the backdrop for destructive confirms and cancels otherwise", async () => {
    renderHost();
    let settled = false;
    const blocked = confirm(discardOptions).then((value) => {
      settled = true;
      return value;
    });
    fireEvent.pointerDown(await screen.findByTestId("confirm-backdrop"));
    fireEvent.click(screen.getByTestId("confirm-backdrop"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(settled).toBe(false);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await expect(blocked).resolves.toBe(false);

    const backdrop = confirm({
      title: "Save without a dry run?",
      description: "The mapping and script have not been tested.",
      confirmLabel: "Save anyway",
      cancelLabel: "Go back",
    });
    fireEvent.click(await screen.findByTestId("confirm-backdrop"));
    await expect(backdrop).resolves.toBe(false);
  });

  it("activates the focused safe button with Enter", async () => {
    renderHost();
    const pending = confirm(discardOptions);
    const keep = await screen.findByRole("button", { name: "Keep editing" });
    await waitFor(() => expect(document.activeElement).toBe(keep));
    fireEvent.keyDown(keep, { key: "Enter" });
    fireEvent.keyUp(keep, { key: "Enter" });
    await expect(pending).resolves.toBe(false);
  });

  it("shows only one confirmation at a time", async () => {
    renderHost();
    const first = confirm({
      title: "First choice",
      confirmLabel: "Continue first",
      cancelLabel: "Stop first",
    });
    const second = confirm({
      title: "Second choice",
      confirmLabel: "Continue second",
      cancelLabel: "Stop second",
    });
    expect(await screen.findAllByRole("alertdialog")).toHaveLength(1);
    expect(screen.queryByText("Second choice")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue first" }));
    await expect(first).resolves.toBe(true);
    expect(await screen.findByText("Second choice")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop second" }));
    await expect(second).resolves.toBe(false);
  });
});
