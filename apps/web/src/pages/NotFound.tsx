import { onLinkClick } from "../router";
import { buttonClass } from "../components/Button";

/** An address no page answers: one heading (the page's own), plain words, a way back. */
export function NotFound() {
  return (
    <section className="rounded-xl border border-line bg-panel px-4 py-4">
      <p className="m-0 text-[14px] text-ink-2">There is no page at this address.</p>
      <a href="/" onClick={onLinkClick("/")}
        className={buttonClass("secondary", "md", "mt-3")}>
        Go to Home
      </a>
    </section>
  );
}
