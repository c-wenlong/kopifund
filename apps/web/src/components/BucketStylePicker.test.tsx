import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { BucketStylePicker } from "./BucketStylePicker";
import type { BucketStyle } from "../types.gen";

const style: BucketStyle = { icon: "utensils", colour: "c1", shown: "c1" } as BucketStyle;

function open(onPick: (c: unknown) => Promise<unknown>, others: Parameters<typeof BucketStylePicker>[0]["others"] = {}) {
  render(<BucketStylePicker name="Food" style={style} place={0} others={others} onPick={onPick} />);
  fireEvent.click(screen.getByRole("button", { name: "Icon and colour for Food" }));
  return {
    colours: within(screen.getByRole("radiogroup", { name: "Colour for Food" })),
    icons: within(screen.getByRole("radiogroup", { name: "Icon for Food" })),
  };
}

describe("BucketStylePicker", () => {
  it("opens a box of colours and icons, showing the bucket's own as picked", () => {
    const { colours, icons } = open(async () => true);
    expect(colours.getAllByRole("radio")).toHaveLength(12);
    expect(colours.getByRole("radio", { name: "Colour 1" })).toBeChecked();
    expect(colours.getByRole("radio", { name: "Colour 2" })).not.toBeChecked();
    expect(icons.getByRole("radio", { name: "utensils" })).toBeChecked();
  });

  it("saves a colour on the pick, shows it picked at once, and keeps the box open for the icon", async () => {
    const onPick = vi.fn(async () => true);
    const { colours, icons } = open(onPick);
    fireEvent.click(colours.getByRole("radio", { name: "Colour 3" }));
    expect(onPick).toHaveBeenCalledWith({ colour: "c3" });
    expect(colours.getByRole("radio", { name: "Colour 3" })).toBeChecked();
    fireEvent.click(icons.getByRole("radio", { name: "plane" }));
    expect(onPick).toHaveBeenLastCalledWith({ icon: "plane" });
    expect(icons.getByRole("radio", { name: "plane" })).toBeChecked();
    // both picks stand until the page reloads the style
    expect(colours.getByRole("radio", { name: "Colour 3" })).toBeChecked();
  });

  it("asks nothing for the colour or icon it already has", () => {
    const onPick = vi.fn(async () => true);
    const { colours, icons } = open(onPick);
    fireEvent.click(colours.getByRole("radio", { name: "Colour 1" }));
    fireEvent.click(icons.getByRole("radio", { name: "utensils" }));
    expect(onPick).not.toHaveBeenCalled();
  });

  it("goes back to the saved colour when the save did not happen", async () => {
    let refuse: (saved: boolean) => void = () => {};
    const { colours } = open(() => new Promise((r) => { refuse = r; }));
    fireEvent.click(colours.getByRole("radio", { name: "Colour 4" }));
    expect(colours.getByRole("radio", { name: "Colour 4" })).toBeChecked();
    await act(async () => refuse(false));
    expect(colours.getByRole("radio", { name: "Colour 4" })).not.toBeChecked();
    expect(colours.getByRole("radio", { name: "Colour 1" })).toBeChecked();
  });

  it("names the other bucket that holds a colour, since picking it swaps the two", () => {
    const { colours } = open(async () => true, { c2: "Groceries" });
    const held = colours.getByRole("radio", { name: "Colour 2, Groceries's" });
    expect(held).toHaveAttribute("title", "Groceries's colour: picking it swaps the two");
    expect(colours.getByRole("radio", { name: "Colour 3" })).not.toHaveAttribute("title");
  });
});
