// G13: the one card. Every panel on a page is this, so the heading is one size
// (14px) everywhere. `region` names the card for a screen reader, for a page
// whose cards are landmarks; `id` is the anchor a card can be linked to.
import { createContext, useContext } from "react";

/** The title of the card around the caller, so a control in its header can name what it controls. */
const CardTitle = createContext<string | null>(null);
export const useCardTitle = () => useContext(CardTitle);

/**
 * A card, or just its contents when `bare`: the dashboard's widget cards
 * (dashboard/Grid.tsx) already supply a border and a heading, so a widget
 * bound there draws bare, while the CardTitle context still names the
 * controls inside. `actions` sit right-aligned above the body in bare mode.
 */
export function MaybeCard({ title, bare = false, actions, children }: {
  title: string; bare?: boolean; actions?: React.ReactNode; children: React.ReactNode;
}) {
  if (!bare) return <Card title={title} actions={actions}>{children}</Card>;
  return (
    <CardTitle.Provider value={title}>
      {actions && <div className="mb-2 flex justify-end">{actions}</div>}
      {children}
    </CardTitle.Provider>
  );
}

// `pinned` keeps the heading, and the actions in it, at the top of the screen (the -top-4 is the page's own top padding)
// on a phone while a long card scrolls under it: the Save of a form too tall
// to see at once.
export function Card({ title, children, actions, id, region = false, pinned = false }: {
  title: string; children: React.ReactNode; actions?: React.ReactNode; id?: string; region?: boolean; pinned?: boolean;
}) {
  const pin = pinned
    ? " max-md:sticky max-md:-top-4 max-md:z-10 max-md:-mx-4 max-md:-mt-3 max-md:rounded-t-xl max-md:border-b max-md:border-line max-md:bg-panel max-md:px-4 max-md:pb-2 max-md:pt-3"
    : "";
  return (
    <CardTitle.Provider value={title}>
      <section id={id} aria-label={region ? title : undefined} className="rounded-xl border border-line bg-panel px-4 py-3">
        <div className={`mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2${pin}`}>
          <h2 className="m-0 text-[14px] font-semibold">{title}</h2>
          {actions}
        </div>
        {children}
      </section>
    </CardTitle.Provider>
  );
}
