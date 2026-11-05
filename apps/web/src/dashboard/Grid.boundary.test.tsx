import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useApi, type DashboardWidget } from "../api";
import { Grid, WIDGET_FAILED } from "./Grid";

// Audit: a widget that throws as it draws stays in its own card; the board,
// the other widgets and the card's Remove button keep working. All names and figures are invented.

/** A widget that reads its answer like the chart widgets do, and throws on one without its series. */
function Reads() {
  const a = useApi<{ series?: number[] }>("/api/dashboard/query?q=test");
  return <p>{a.data ? `series ${a.data.series!.length}` : "Loading"}</p>;
}

vi.mock("./registry", () => ({
  widgetDef: (type: string) => ({
    type, title: type, blurb: "", size: { w: 4, h: 1 },
    component: type === "bad"
      ? () => { throw new Error("an answer in a shape this build does not know"); }
      : type === "reads" ? Reads
      : ({ widget }: { widget: DashboardWidget }) => <p>{`drawn ${widget.id}`}</p>,
  }),
}));

const widget = (id: string, type: string, x: number): DashboardWidget =>
  ({ id, type, title: `Widget ${id}`, layout: { x, y: 0, w: 4, h: 1 } });

beforeEach(() => {
  // React reports each caught error to the console; the tests only check what is drawn
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

it("holds a widget that throws to its own card", () => {
  const onRemove = vi.fn();
  render(<Grid widgets={[widget("a", "bad", 0), widget("b", "good", 4)]} interactive={false}
    onArrange={() => {}} onRemove={onRemove} />);
  const bad = screen.getByRole("article", { name: "Widget a" });
  expect(within(bad).getByRole("alert")).toHaveTextContent(WIDGET_FAILED);
  within(bad).getByRole("button", { name: "Remove Widget a" }).click();
  expect(onRemove).toHaveBeenCalledWith("a");
  expect(screen.getByText("drawn b")).toBeInTheDocument();
});

// Greptile on #43: a reset alone mounted the widget on the kept answer it had just failed on
it("draws the widget from the server's new answer on Try again, not the kept bad one", async () => {
  let answer: unknown = {};
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(answer), { status: 200 }));
  render(<Grid widgets={[widget("a", "reads", 0)]} interactive={false} onArrange={() => {}} onRemove={() => {}} />);
  expect(await screen.findByText(WIDGET_FAILED)).toBeInTheDocument();
  answer = { series: [1, 2] };
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("series 2")).toBeInTheDocument();
});
