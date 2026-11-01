import type { Bank } from "../accounts";

// Each bank's own app icon (supplied by the owner, 64 px in web/public/banks),
// else a tile in the bank's colour (tokens in index.css) with its initial. The
// logos are the banks' trademarks, used only to label the owner's own accounts.
// Trust Bank is listed greyed out as coming soon until it gets a statement parser.
const LOGOS: Partial<Record<Bank, string>> = {
  dbs: "/banks/dbs.png",
  posb: "/banks/posb.png",
  paylah: "/banks/paylah.png",
  trust: "/banks/trust.png",
  youtrip: "/banks/youtrip.png",
  revolut: "/banks/revolut.png",
  wise: "/banks/wise.png",
  tiger: "/banks/tiger.png",
  cpf: "/banks/cpf.png",
};

const TILES: Partial<Record<Bank, { text: string; bg: string; fg: string }>> = {
  ocbc: { text: "O", bg: "var(--bank-ocbc)", fg: "var(--bank-ink)" },
  // G21: a plain initial on the bank's blue, never a copy of its logo
  uob: { text: "U", bg: "var(--bank-uob)", fg: "var(--bank-ink)" },
};
const OTHER = { text: "•", bg: "var(--tile)", fg: "var(--ink-2)" };

export function BankMark({ bank }: { bank: Bank }) {
  const logo = LOGOS[bank];
  if (logo) {
    return <img src={logo} alt="" aria-hidden data-bank={bank} className="size-5 shrink-0 rounded object-contain" />;
  }
  const m = TILES[bank] ?? OTHER;
  return (
    // the faint inset line keeps a dark tile visible on the dark theme
    <span aria-hidden data-bank={bank} style={{ background: m.bg, color: m.fg, boxShadow: "inset 0 0 0 1px var(--line)" }}
      className="grid size-5 shrink-0 place-items-center rounded text-[11px] font-bold leading-none">
      {m.text}
    </span>
  );
}
