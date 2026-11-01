import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { navigate, onLinkClick, usePath, useUnsavedGuard } from "./router";
import { LeaveBar } from "./components/LeaveBar";

// G18: a page with a form, its dirty state in the test's hands, inside a shell
// that hosts the question the way App does
function Shell({ dirty }: { dirty: boolean }) {
  const path = usePath();
  return (
    <>
      <a href="/plan" onClick={onLinkClick("/plan")}>Plan</a>
      <LeaveBar />
      <p>On {path}</p>
      {path === "/net-worth" && <Form dirty={dirty} />}
    </>
  );
}

function Form({ dirty }: { dirty: boolean }) {
  useUnsavedGuard(dirty);
  return <input aria-label="Pay" />;
}

const where = () => window.location.pathname;

beforeEach(() => {
  // every test starts on Net worth, on a fresh history entry of the router's own
  act(() => navigate("/net-worth"));
});

describe("the unsaved guard", () => {
  it("lets a move through when nothing is unsaved", () => {
    render(<Shell dirty={false} />);
    act(() => navigate("/plan"));
    expect(where()).toBe("/plan");
    expect(screen.getByText("On /plan")).toBeInTheDocument();
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
  });

  it("holds navigate() and asks, while something is unsaved", () => {
    render(<Shell dirty />);
    act(() => navigate("/plan"));
    expect(where()).toBe("/net-worth");
    expect(screen.getByText("On /net-worth")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Leave without saving?");
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
  });

  it("holds a link click the same way", async () => {
    const user = userEvent.setup();
    render(<Shell dirty />);
    await user.click(screen.getByRole("link", { name: "Plan" }));
    expect(where()).toBe("/net-worth");
    expect(screen.getByText("Leave without saving?")).toBeInTheDocument();
  });

  it("goes on with the move on Leave", async () => {
    const user = userEvent.setup();
    render(<Shell dirty />);
    act(() => navigate("/plan"));
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(where()).toBe("/plan");
    expect(screen.getByText("On /plan")).toBeInTheDocument();
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
  });

  it("stays put on Keep editing, and on Escape", async () => {
    const user = userEvent.setup();
    render(<Shell dirty />);
    act(() => navigate("/plan"));
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(where()).toBe("/net-worth");
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
    act(() => navigate("/plan"));
    await user.keyboard("{Escape}");
    expect(where()).toBe("/net-worth");
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
  });

  it("drops the question once the edits are saved or discarded on the page", () => {
    const { rerender } = render(<Shell dirty />);
    act(() => navigate("/plan"));
    expect(screen.getByText("Leave without saving?")).toBeInTheDocument();
    rerender(<Shell dirty={false} />);
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
    expect(where()).toBe("/net-worth");
  });

  it("sets beforeunload only while something is unsaved", () => {
    const unload = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    const { rerender, unmount } = render(<Shell dirty={false} />);
    expect(unload()).toBe(false);
    rerender(<Shell dirty />);
    expect(unload()).toBe(true);
    rerender(<Shell dirty={false} />);
    expect(unload()).toBe(false);
    rerender(<Shell dirty />);
    unmount();
    expect(unload()).toBe(false);
  });

  it("holds the browser's Back, puts the page back, and goes back on Leave", async () => {
    act(() => navigate("/plan"));
    act(() => navigate("/net-worth"));
    const user = userEvent.setup();
    render(<Shell dirty />);
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByText("Leave without saving?")).toBeInTheDocument());
    // the step back is undone, and the page never heard of it
    await waitFor(() => expect(where()).toBe("/net-worth"));
    expect(screen.getByText("On /net-worth")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(where()).toBe("/plan"));
    await waitFor(() => expect(screen.getByText("On /plan")).toBeInTheDocument());
  });

  it("lets Back through when nothing is unsaved", async () => {
    act(() => navigate("/plan"));
    act(() => navigate("/net-worth"));
    render(<Shell dirty={false} />);
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByText("On /plan")).toBeInTheDocument());
    expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
  });
});
