import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Toaster } from "./Toast";
import { useAction, type RunOptions } from "./useAction";

// a card with one button whose write the test controls
function Probe({ write, options, onDone }: {
  write: () => Promise<unknown>; options?: RunOptions; onDone?: (ok: boolean) => void;
}) {
  const action = useAction();
  return (
    <div>
      <button disabled={action.running} onClick={() => void action.run("one", write, options).then(onDone)}>Go</button>
      <span data-testid="busy">{action.busy ?? "none"}</span>
      {action.failed && <span role="alert">{action.failed.key}: {action.failed.message}</span>}
      <button onClick={action.clear}>Clear</button>
    </div>
  );
}

function deferred() {
  let resolve!: () => void, reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("useAction", () => {
  it("marks the key busy while the write runs and answers true when it lands", async () => {
    const d = deferred();
    const onDone = vi.fn();
    render(<Probe write={() => d.promise} onDone={onDone} />);
    act(() => screen.getByText("Go").click());
    expect(screen.getByTestId("busy")).toHaveTextContent("one");
    expect(screen.getByText("Go")).toBeDisabled();
    await act(async () => d.resolve());
    expect(screen.getByTestId("busy")).toHaveTextContent("none");
    expect(onDone).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a failure under its key, and a new run clears it", async () => {
    let fail = true;
    const onDone = vi.fn();
    render(<Probe write={async () => { if (fail) throw new Error("refused"); }} onDone={onDone} />);
    await act(async () => screen.getByText("Go").click());
    expect(screen.getByRole("alert")).toHaveTextContent("one: refused");
    expect(onDone).toHaveBeenLastCalledWith(false);
    fail = false;
    await act(async () => screen.getByText("Go").click());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(onDone).toHaveBeenLastCalledWith(true);
  });

  it("clear drops the failure", async () => {
    render(<Probe write={async () => { throw new Error("refused"); }} />);
    await act(async () => screen.getByText("Go").click());
    act(() => screen.getByText("Clear").click());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sends a failure to a toast instead when asked", async () => {
    render(<><Toaster /><Probe write={async () => { throw new Error("no answer"); }} options={{ toast: true }} /></>);
    await act(async () => screen.getByText("Go").click());
    expect(await screen.findByText("no answer")).toBeInTheDocument();
    expect(screen.queryByText(/one: no answer/)).toBeNull();
  });

  it("hands a failure to onError instead when given one", async () => {
    const onError = vi.fn();
    render(<Probe write={async () => { throw new Error("gone"); }} options={{ onError }} />);
    await act(async () => screen.getByText("Go").click());
    expect(onError).toHaveBeenCalledWith("gone");
    expect(screen.queryByText(/one: gone/)).toBeNull();
  });
});
