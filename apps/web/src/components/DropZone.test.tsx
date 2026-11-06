import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DropZone } from "./DropZone";

const pdf = (name: string) => new File(["%PDF"], name, { type: "application/pdf" });
const input = () => screen.getByLabelText("Choose PDFs", { selector: "input" });

describe("DropZone", () => {
  it("hands the chosen files on, and clears the picker so the same file can be chosen again", async () => {
    const onFiles = vi.fn();
    render(<DropZone disabled={false} reason="Choose an account first" onFiles={onFiles} />);
    await userEvent.upload(input(), pdf("a.pdf"));
    expect(onFiles).toHaveBeenCalledOnce();
    expect(onFiles.mock.calls[0][0][0].name).toBe("a.pdf");
    // the same file chosen again must still be a change, so the picker is emptied
    expect((input() as HTMLInputElement).files).toHaveLength(0);
  });

  it("hands on the files dropped on it", () => {
    const onFiles = vi.fn();
    render(<DropZone disabled={false} reason="Choose an account first" onFiles={onFiles} />);
    fireEvent.drop(screen.getByTestId("drop-zone"), { dataTransfer: { files: [pdf("b.pdf")] } });
    expect(onFiles.mock.calls[0][0][0].name).toBe("b.pdf");
  });

  it("says why nothing can be added while disabled, takes no file, and stays in the danger colour once a drop was refused", () => {
    const onFiles = vi.fn();
    render(<DropZone disabled reason="Choose an account first" onFiles={onFiles} />);
    expect(input()).toBeDisabled();
    const why = screen.getByText("Choose an account first");
    expect(why).not.toHaveClass("text-danger");
    fireEvent.drop(screen.getByTestId("drop-zone"), { dataTransfer: { files: [pdf("c.pdf")] } });
    expect(onFiles).not.toHaveBeenCalled();
    expect(why).toHaveClass("text-danger");
  });

  it("invites a drop only while it can take one", () => {
    const { rerender } = render(<DropZone disabled={false} reason="why" onFiles={() => {}} />);
    expect(screen.getByText("or drop them here")).toBeInTheDocument();
    rerender(<DropZone disabled reason="why" onFiles={() => {}} />);
    expect(screen.queryByText("or drop them here")).not.toBeInTheDocument();
  });
});
