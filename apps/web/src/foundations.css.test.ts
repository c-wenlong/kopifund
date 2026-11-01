import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { REST_COLOUR, SERIES_COLOURS } from "./components/chartKit";

// G13: the global stylesheet is plain text to the tests, so the few rules that matter
// to behaviour are checked here, against the same markup the app renders.
// (Vitest runs from web/, and gives a stylesheet import no text, so the file is read.)
const css = readFileSync("src/index.css", "utf8");

/** The selector of the rule inside `@media (pointer: coarse)` that sizes icon buttons. */
function iconButtonSelector() {
  const coarse = css.slice(css.indexOf("@media (pointer: coarse)"));
  const at = coarse.indexOf("min-width: 40px");
  const head = coarse.slice(0, at);
  return head.slice(head.lastIndexOf("}") + 1, head.lastIndexOf("{")).replace(/\/\*[\s\S]*?\*\//g, "").trim();
}

describe("the touch target for icon buttons", () => {
  const selector = iconButtonSelector();
  const make = (html: string) => {
    document.body.innerHTML = html;
    return document.body.firstElementChild as HTMLElement;
  };

  it("is for a button that holds only an icon, which is labelled", () => {
    expect(selector).toContain("[aria-label]");
    expect(make('<button aria-label="Edit trip"><svg></svg></button>').matches(selector)).toBe(true);
  });

  it("leaves a button with words and an icon alone, as the Show all rows are", () => {
    // a text node is not a child element, so a bare :has(> svg:only-child) would match this too
    expect(make('<button>Show all 12<svg></svg></button>').matches(selector)).toBe(false);
    expect(make('<button><svg></svg> Add rule</button>').matches(selector)).toBe(false);
  });

  it("never appears in the stylesheet without the label condition", () => {
    const bare = css.split("\n").filter((l) => l.includes("button:has(> svg:only-child)") && !l.includes("[aria-label]"));
    expect(bare).toEqual([]);
  });
});

describe("the calendar's keyboard focus ring", () => {
  it("is the app's focus colour, not a series colour", () => {
    const line = css.split("\n").find((l) => l.includes(".rdp-day_button:focus-visible")) ?? "";
    expect(line).toContain("var(--focus)");
    expect(line).not.toContain("--c1");
  });
});

// G18: the series colours. Every chart draws its series from SERIES_COLOURS, so the list has to hold enough
// colours (a month of spending has nine buckets), each of them a real token in both themes, each easy to
// see on the card it sits on, and no two close enough to be taken for one another.
describe("the series colours", () => {
  const lightStart = css.indexOf(":root {");
  const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");
  const themes = {
    light: css.slice(lightStart, darkStart),
    dark: css.slice(darkStart, css.indexOf("\n}\n", darkStart)),
  };
  const tokens = (body: string) => new Map([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((m) => [m[1], m[2]]));
  const tokenOf = (theme: keyof typeof themes, ref: string) => {
    const name = /^var\(--([\w-]+)\)$/.exec(ref)?.[1] ?? "";
    return tokens(themes[theme]).get(name) ?? "";
  };

  const channel = (hex: string, at: number) => {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const xyz = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => channel(hex, i));
    return [0.4124 * r + 0.3576 * g + 0.1805 * b, 0.2126 * r + 0.7152 * g + 0.0722 * b, 0.0193 * r + 0.1192 * g + 0.9505 * b];
  };
  /** WCAG contrast ratio of two colours. */
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [xyz(a)[1], xyz(b)[1]].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  /** CIE76 distance of two colours: about 2 is the least a trained eye can tell, 20 or more is plainly two colours. */
  const distance = (a: string, b: string) => {
    const lab = (hex: string) => {
      const [x, y, z] = xyz(hex);
      const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
      const [fx, fy, fz] = [f(x / 0.95047), f(y), f(z / 1.08883)];
      return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
    };
    return Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]));
  };

  it("hold at least twelve, each its own token", () => {
    expect(SERIES_COLOURS.length).toBeGreaterThanOrEqual(12);
    expect(new Set(SERIES_COLOURS).size).toBe(SERIES_COLOURS.length);
    expect(SERIES_COLOURS[0]).toBe("var(--c1)");
  });

  for (const theme of ["light", "dark"] as const) {
    const panel = tokens(themes[theme]).get("panel") ?? "";
    const colours = SERIES_COLOURS.map((ref) => tokenOf(theme, ref));

    it(`are all defined in the ${theme} theme`, () => {
      expect(panel).toMatch(/^#/);
      expect(colours.filter((c) => !c)).toEqual([]);
      expect(tokenOf(theme, REST_COLOUR)).toMatch(/^#/);
    });

    it(`show at 3:1 or better against the ${theme} card, as a chart mark must`, () => {
      for (const [i, c] of colours.entries()) expect(contrast(c, panel), `--c${i + 1} ${c}`).toBeGreaterThanOrEqual(3);
      expect(contrast(tokenOf(theme, REST_COLOUR), panel)).toBeGreaterThanOrEqual(3);
    });

    it(`are told apart by eye in the ${theme} theme, the folded "others" neutral included`, () => {
      const all = [...colours, tokenOf(theme, REST_COLOUR)];
      for (let i = 0; i < all.length; i++) {
        for (let j = i + 1; j < all.length; j++) {
          expect(distance(all[i], all[j]), `${all[i]} against ${all[j]}`).toBeGreaterThanOrEqual(18);
        }
      }
    });
  }
});
