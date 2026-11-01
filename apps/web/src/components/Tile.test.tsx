import { render, screen } from "@testing-library/react";
import { Progress, RangeTrack, Sparkline, Tile, TileGrid } from "./Tile";

describe("Tile", () => {
  it("colours the figure itself when it is warn or danger, so a bad number does not look healthy", () => {
    render(<>
      <Tile label="A" value="1" tone="danger" note="below" />
      <Tile label="B" value="2" tone="warn" />
      <Tile label="C" value="3" tone="good" note="up" />
      <Tile label="D" value="4" />
    </>);
    expect(screen.getByText("1")).toHaveClass("text-danger");
    expect(screen.getByText("below")).toHaveClass("text-danger");
    expect(screen.getByText("2")).toHaveClass("text-warn");
    // good colours the note, never the figure
    expect(screen.getByText("3")).not.toHaveClass("text-good");
    expect(screen.getByText("up")).toHaveClass("text-good");
    expect(screen.getByText("4")).not.toHaveClass("text-warn");
    expect(screen.getByText("4")).not.toHaveClass("text-danger");
  });

  it("colours a note on its own with noteTone, and shows a visual under it", () => {
    render(<Tile label="Spent" value="S$9" note="+S$1 on the average" noteTone="warn" visual={<i data-testid="v" />} />);
    expect(screen.getByText("S$9")).not.toHaveClass("text-warn");
    expect(screen.getByText("+S$1 on the average")).toHaveClass("text-warn");
    expect(screen.getByTestId("v")).toBeInTheDocument();
  });

  it("lays tiles out with one gap", () => {
    render(<TileGrid cols={3} label="Figures"><Tile label="A" value="1" /></TileGrid>);
    expect(screen.getByRole("group", { name: "Figures" })).toHaveClass("gap-3", "lg:grid-cols-3");
  });
});

describe("Sparkline", () => {
  it("draws one point per month and says where it starts and ends", () => {
    const { container } = render(<Sparkline values={[1, 3, 2, 5]} label="Cash, Jan S$1 to Apr S$5" />);
    expect(screen.getByRole("img", { name: "Cash, Jan S$1 to Apr S$5" })).toBeInTheDocument();
    expect(container.querySelector("polyline")!.getAttribute("points")!.split(" ")).toHaveLength(4);
  });

  it("draws a flat line mid-height and nothing for fewer than two points", () => {
    const { container, rerender } = render(<Sparkline values={[4, 4, 4]} label="flat" />);
    const ys = container.querySelector("polyline")!.getAttribute("points")!.split(" ").map((p) => p.split(",")[1]);
    expect(new Set(ys).size).toBe(1);
    rerender(<Sparkline values={[4]} label="one" />);
    expect(container.querySelector("polyline")).toBeNull();
  });
});

describe("RangeTrack and Progress", () => {
  it("marks the figure on a track with its range shaded, and names both", () => {
    render(<RangeTrack value={1} low={3} high={6} tone="danger" label="1.0 months; 3 to 6 shaded" />);
    expect(screen.getByRole("img", { name: "1.0 months; 3 to 6 shaded" })).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
  });

  it("fills a progress bar to the share and never past the whole", () => {
    const { rerender } = render(<Progress value={25} of={100} label="paid" />);
    expect(screen.getByRole("progressbar", { name: "paid" })).toHaveAttribute("aria-valuenow", "25");
    rerender(<Progress value={150} of={100} label="paid" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    rerender(<Progress value={5} of={0} label="paid" />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

describe("RangeTrack ticks", () => {
  it("sets the end figures in tabular numerals, like every other figure", () => {
    render(<RangeTrack value={1} low={3} high={6} tone="warn" label="range" />);
    expect(screen.getByText("3")).toHaveClass("num");
    expect(screen.getByText("6")).toHaveClass("num");
  });
});
