import { useCallback, useEffect, useRef, useState } from "react";
import type { BucketIcon, BucketStyle, SeriesColour } from "../types.gen";
import { BUCKET_COLOURS, BUCKET_ICON, BUCKET_ICONS, bucketLook, BucketMark, tokenColour } from "./BucketMark";
import { Button } from "./Button";
import { Popover } from "./Popover";

// G18: a bucket's icon and colour, picked on the Categories page. The button
// shows the bucket's icon in its colour and opens a box of the twelve chart
// colours and the icons; a pick is saved at once and the box stays open for the
// other. A colour another bucket is drawn in is marked with that bucket's name
// in its tooltip: picking it swaps the two (finance/categories.py).

const cell = "flex size-8 items-center justify-center rounded-md";

type Change = { icon: BucketIcon } | { colour: SeriesColour };
type Picked = { icon?: BucketIcon; colour?: SeriesColour };

export function BucketStylePicker({ name, style, place, others, onPick }: {
  name: string;
  style: BucketStyle;
  /** the bucket's place among all buckets, for an answer with no `shown` */
  place: number;
  /** each other bucket's name by the colour it is drawn in */
  others: Partial<Record<SeriesColour, string>>;
  /** saves the pick; resolves false when it was not saved */
  onPick: (change: Change) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  // What was picked and is not on the page yet: it shows as picked at once, so a second pick made
  // before the first is saved compares with it, not with the saved style, and the last pick wins.
  // Once no pick is on its way, the bucket's style as the page reloads it is what shows.
  const [picked, setPicked] = useState<Picked>({});
  const waiting = useRef(0);
  useEffect(() => {
    if (waiting.current === 0) setPicked({});
  }, [style]);
  const colourOn = picked.colour ?? style.shown;
  const iconOn = picked.icon ?? style.icon;
  const look = bucketLook({ ...style, icon: iconOn, shown: colourOn }, place);

  const pick = (change: Change) => {
    setPicked((p) => ({ ...p, ...change }));
    waiting.current += 1;
    void onPick(change).then((saved) => {
      waiting.current -= 1;
      // not saved: the box goes back to what the record holds
      if (saved === false) {
        setPicked((p) => ("colour" in change
          ? (p.colour === change.colour ? { ...p, colour: undefined } : p)
          : (p.icon === change.icon ? { ...p, icon: undefined } : p)));
      }
    });
  };
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) anchor.current?.focus();
  }, []);
  const label = `Icon and colour for ${name}`;
  return (
    <>
      <Button ref={anchor} size="icon" aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}>
        <BucketMark look={look} size={16} />
      </Button>
      {open && (
        <Popover anchor={anchor} label={label} onClose={close} links>
          <div className="flex flex-col gap-2">
            <div role="radiogroup" aria-label={`Colour for ${name}`} className="grid grid-cols-6 gap-1">
              {BUCKET_COLOURS.map((c, i) => {
                const on = colourOn === c;
                const holder = on ? undefined : others[c];
                const what = `Colour ${i + 1}${holder ? `, ${holder}'s` : ""}`;
                return (
                  <button key={c} type="button" role="radio" aria-checked={on} aria-label={what}
                    title={holder ? `${holder}'s colour: picking it swaps the two` : undefined}
                    onClick={() => { if (!on) pick({ colour: c }); }}
                    className={`${cell} hover:bg-hover`}>
                    <span aria-hidden className={`size-5 rounded-full ${on ? "ring-2 ring-ink ring-offset-2 ring-offset-panel" : ""}`}
                      style={{ background: tokenColour(c) }} />
                  </button>
                );
              })}
            </div>
            <div className="border-t border-line" />
            <div role="radiogroup" aria-label={`Icon for ${name}`} className="grid grid-cols-6 gap-1">
              {BUCKET_ICONS.map((icon) => {
                const on = iconOn === icon;
                const Icon = BUCKET_ICON[icon];
                return (
                  <button key={icon} type="button" role="radio" aria-checked={on} aria-label={icon.replace(/-/g, " ")}
                    onClick={() => { if (!on) pick({ icon }); }}
                    className={`${cell} border ${on ? "border-ink bg-hover" : "border-transparent text-ink-2 hover:bg-hover hover:text-ink"}`}>
                    <Icon size={16} aria-hidden focusable={false} style={on ? { color: look.colour } : undefined} />
                  </button>
                );
              })}
            </div>
          </div>
        </Popover>
      )}
    </>
  );
}
