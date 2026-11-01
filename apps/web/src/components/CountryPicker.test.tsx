import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Country } from "../api";
import { CountryPicker, countryName, unknownCodes } from "./CountryPicker";
import { Chip } from "./Chip";

const LIST: Country[] = [
  { code: "JPN", alpha2: "JP", flag: "\u{1F1EF}\u{1F1F5}" },
  { code: "MYS", alpha2: "MY", flag: "\u{1F1F2}\u{1F1FE}" },
  { code: "GBR", alpha2: "GB", flag: "\u{1F1EC}\u{1F1E7}" },
];

function Harness({ start = [], countries = LIST }: { start?: string[]; countries?: Country[] | null }) {
  const [codes, setCodes] = useState(start);
  return (
    <>
      <CountryPicker value={codes} countries={countries} onChange={setCodes} />
      <output data-testid="codes">{codes.join(",")}</output>
    </>
  );
}

describe("CountryPicker", () => {
  it("names a country from its region code, and falls back to the code", () => {
    expect(countryName({ code: "GBR", alpha2: "GB" })).toBe("United Kingdom");
    expect(countryName({ code: "ZZZ", alpha2: "not a region" })).toBe("ZZZ");
  });

  it("lists the codes the country list does not hold, and none before the list has loaded", () => {
    expect(unknownCodes(["JPN", "XXX", "QQQ"], LIST)).toEqual(["XXX", "QQQ"]);
    expect(unknownCodes(["XXX"], null)).toEqual([]);
  });

  it("offers countries by name, leaves out the ones already chosen, and adds in the order chosen", () => {
    render(<Harness start={["MYS"]} />);
    const box = screen.getByRole("combobox", { name: "Add a country" });
    fireEvent.focus(box);
    expect(screen.getAllByRole("option").map((o) => o.textContent))
      .toEqual(["\u{1F1EF}\u{1F1F5} Japan (JPN)", "\u{1F1EC}\u{1F1E7} United Kingdom (GBR)"]);
    fireEvent.mouseDown(screen.getAllByRole("option")[1]);
    fireEvent.change(box, { target: { value: "jpn" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByTestId("codes")).toHaveTextContent("MYS,GBR,JPN");
  });

  it("opens the list when the box is clicked", () => {
    render(<Harness />);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("combobox", { name: "Add a country" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("marks a code that is no country, with how to fix it, and a country the list knows is fine", () => {
    render(<Harness start={["JPN", "XXX"]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("XXX is not an ISO country code. Remove it to save the trip.");
    const chips = within(screen.getByRole("list", { name: "Chosen countries" }));
    expect(chips.getByText("Japan")).toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Remove XXX" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("codes")).toHaveTextContent("JPN");
  });

  it("waits for the list: the box is off and nothing is called unknown", () => {
    render(<Harness start={["XXX"]} countries={null} />);
    expect(screen.getByRole("combobox", { name: "Add a country" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Chip", () => {
  it("is one small label", () => {
    render(<Chip tone="info">Planned</Chip>);
    expect(screen.getByText("Planned")).toHaveClass("bg-info-bg", "text-info");
  });
});
