import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Field } from "./Field";

describe("Field", () => {
  it("ties its label to the box it draws, and shows a chip under it", () => {
    render(<Field label="Insurance" chip={<button>Use S$120</button>}>{(id) => <input id={id} />}</Field>);
    expect(screen.getByLabelText("Insurance")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByRole("button", { name: "Use S$120" })).toBeInTheDocument();
  });

  it("gives each field its own id", () => {
    render(<>
      <Field label="From">{(id) => <input id={id} />}</Field>
      <Field label="To">{(id) => <input id={id} />}</Field>
    </>);
    expect(screen.getByLabelText("From")).not.toBe(screen.getByLabelText("To"));
  });
});
