import { render, screen, within } from "@testing-library/react";
import type { DashboardWidget } from "../api";
import { Grid, WIDGET_FAILED } from "./Grid";

// Audit: a widget that throws as it draws stays in its own card; the board,
// the other widgets and the card's Remove button keep working. All names are invented.

vi.mock("./registry", () => ({
  widgetDef: (type: string) => ({
    type, title: type, blurb: "", size: { w: 4, h: 2 },
    component: type === "bad"
      ? () => { throw new Error("an answer in a shape this build does not know"); }
      : ({ widget }: { widget: DashboardWidget }) => <p>{`drawn ${widget.id}`}</p>,
  }),
}));

const widget = (id: string, type: string, x: number): DashboardWidget =>
  ({ id, type, title: `Widget ${id}`, layout: { x, y: 0, w: 4, h: 2 } });

it("holds a widget that throws to its own card", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const onRemove = vi.fn();
  render(<Grid widgets={[widget("a", "bad", 0), widget("b", "good", 4)]} interactive={false}
    onArrange={() => {}} onRemove={onRemove} />);
  const bad = screen.getByRole("article", { name: "Widget a" });
  expect(within(bad).getByRole("alert")).toHaveTextContent(WIDGET_FAILED);
  within(bad).getByRole("button", { name: "Remove Widget a" }).click();
  expect(onRemove).toHaveBeenCalledWith("a");
  expect(screen.getByText("drawn b")).toBeInTheDocument();
  vi.restoreAllMocks();
});
