import { fireEvent, screen } from "@testing-library/react";

/**
 * G18: choose `option` in the app's Select whose box (its button) is `box`, as a
 * person does: open it, press the option whose label is exactly `option`. The
 * app draws no native <select>, so a test cannot `fireEvent.change` one.
 */
export function choose(box: HTMLElement, option: string) {
  fireEvent.click(box);
  const hit = screen.getAllByRole("option")
    .find((o) => [...o.querySelectorAll("span")].some((s) => s.textContent === option));
  if (!hit) throw new Error(`No option "${option}" in ${box.getAttribute("aria-label") ?? "the select"}`);
  fireEvent.mouseDown(hit);
}
