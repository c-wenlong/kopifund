import { render, screen } from "@testing-library/react";
import { EyeList } from "./NeedsYourEye";
import type { EyeLine } from "../api";

describe("EyeList", () => {
  // Audit: a kind a newer server sends has no icon in this build; the line still draws
  it("draws a line whose kind this build does not know", () => {
    const lines = [{ kind: "brand_new_kind", tone: "warn", text: "Something new to look at", to: "/" }] as unknown as EyeLine[];
    render(<EyeList lines={lines} />);
    expect(screen.getByRole("link", { name: /Something new to look at/ })).toBeInTheDocument();
  });
});
