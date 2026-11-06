import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OFFLINE } from "../api";
import { Loaded, NoStatementsYet } from "./States";

// Audit: the first-answer states every view shares

describe("Loaded", () => {
  const draw = (n: number) => <p>Got {n}</p>;

  it("says what is loading while the first answer is on its way", () => {
    render(<Loaded state={{ data: null, error: null, loading: true }} what="Loading your plan">{draw}</Loaded>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading your plan");
  });

  it("draws the answer once it is here", () => {
    render(<Loaded state={{ data: 4, error: null, loading: false }} what="Loading">{draw}</Loaded>);
    expect(screen.getByText("Got 4")).toBeInTheDocument();
  });

  it("says what went wrong, with the hint and Try again", () => {
    render(<Loaded state={{ data: null, error: "Server said no", loading: false }} what="Loading" hint="Look again later">{draw}</Loaded>);
    expect(screen.getByRole("alert")).toHaveTextContent("Server said no");
    expect(screen.getByText("Look again later")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("names what is missing when the answer is empty", () => {
    render(<Loaded state={{ data: null, error: null, loading: false }} what="Loading" missing="No profile">{draw}</Loaded>);
    expect(screen.getByRole("alert")).toHaveTextContent("No profile");
  });

  it("stays a quiet placeholder when the server is simply not there", () => {
    render(<Loaded state={{ data: null, error: OFFLINE, loading: false }} what="Loading">{draw}</Loaded>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("wraps the waiting and the problem, never the answer, in className", () => {
    const { container, rerender } = render(
      <Loaded state={{ data: null, error: null, loading: true }} what="Loading" className="px-4">{draw}</Loaded>);
    expect(container.firstElementChild).toHaveClass("px-4");
    rerender(<Loaded state={{ data: 2, error: null, loading: false }} what="Loading" className="px-4">{draw}</Loaded>);
    expect(container.firstElementChild?.tagName).toBe("P");
  });
});

describe("NoStatementsYet", () => {
  it("says what comes with the first statements and links to Upload", () => {
    render(<NoStatementsYet title="No transactions yet" what="Transactions" />);
    expect(screen.getByText("No transactions yet")).toBeInTheDocument();
    expect(screen.getByText("Transactions come with your first statements.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Load your statements" })).toHaveAttribute("href", "/upload");
  });
});
