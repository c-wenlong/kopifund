import { render, screen, within } from "@testing-library/react";
import { UploadStepper, type StepItem } from "./UploadStepper";

const steps: StepItem[] = [
  { key: "add", step: "upload", label: "Add", state: "done", note: "3 files" },
  { key: "check", step: "check", label: "Check", state: "running" },
  { key: "load", step: "load", label: "Load", state: "blocked", note: "1 problem" },
  { key: "mirror", step: "mirror", label: "Mirror", state: "pending" },
];

describe("UploadStepper", () => {
  it("lists the steps in order, each saying in words how it stands", () => {
    render(<UploadStepper steps={steps} />);
    const items = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "Add: done3 files", "Check: running", "Load: blocked1 problem", "Mirror: not started",
    ]);
  });

  it("shows only the steps it is given, so a step left out (Mirror without Notion) is not drawn", () => {
    render(<UploadStepper steps={steps.slice(0, 3)} />);
    expect(screen.queryByText(/Mirror/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("marks a step's state for the page to read", () => {
    render(<UploadStepper steps={steps} />);
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("data-state"))).toEqual(["done", "running", "blocked", "pending"]);
  });
});
