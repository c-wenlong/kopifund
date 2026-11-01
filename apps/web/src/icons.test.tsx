import { render, screen } from "@testing-library/react";
import type { LucideIcon } from "lucide-react";
import {
  CADENCE_ICON, COMING_ICON, EYE_ICON, KIND_ICON, KindIcon, KindLabel, NEED_ICON, NEEDS_ACCOUNT_ICON, NOT_SUPPORTED_ICON, PAYEE_KIND_ICON, RUNNING_ICON,
  SCAM_FLAG_ICON, SCAM_ICON, SERVER_ICON, STEP_ICON, STEP_STATUS_ICON, TRIP_ICON, TRIP_TAG_ICON,
} from "./icons";
import { leadingIcon } from "./test-icons";

// G18: every map is a Record over its union, so the typecheck already fails on
// a value with no icon. These check the rest: each icon draws, one icon means
// one thing across the maps, and the icon is hidden or named as it should be.

const MAPS: Record<string, Record<string, LucideIcon>> = {
  kind: KIND_ICON, need: NEED_ICON, cadence: CADENCE_ICON, coming: COMING_ICON, step: STEP_ICON, stepStatus: STEP_STATUS_ICON,
  scam: SCAM_ICON, payeeKind: PAYEE_KIND_ICON, tripTag: TRIP_TAG_ICON, eye: EYE_ICON, server: SERVER_ICON,
  one: { running: RUNNING_ICON, needsAccount: NEEDS_ACCOUNT_ICON, notSupported: NOT_SUPPORTED_ICON, scamFlag: SCAM_FLAG_ICON, trip: TRIP_ICON },
};

// the one meaning each shared icon has: the same thing named in two places
const SAME: string[][] = [
  ["kind.income", "need.Income"],
  ["kind.transfer", "need.Transfer"],
  ["coming.late", "eye.recurring_off"],
  ["one.running", "server.checking"],
  ["one.scamFlag", "eye.scam"],
];

const entries = Object.entries(MAPS).flatMap(([map, icons]) => Object.entries(icons).map(([key, icon]) => ({ name: `${map}.${key}`, icon })));

describe("the kind icons", () => {
  it("draws every icon in every map", () => {
    for (const { name, icon } of entries) {
      const { container, unmount } = render(<KindIcon icon={icon} />);
      expect(container.querySelector("svg"), name).not.toBeNull();
      unmount();
    }
  });

  it("gives one icon one meaning across the maps", () => {
    const meaning = (name: string) => SAME.find((group) => group.includes(name))?.[0] ?? name;
    const byIcon = new Map<LucideIcon, Set<string>>();
    for (const { name, icon } of entries) byIcon.set(icon, (byIcon.get(icon) ?? new Set()).add(meaning(name)));
    const twice = [...byIcon.values()].filter((names) => names.size > 1).map((names) => [...names].join(" and "));
    expect(twice).toEqual([]);
  });

  it("gives every value of a map its own icon", () => {
    for (const [map, icons] of Object.entries(MAPS)) {
      if (map === "one") continue;
      const list = Object.values(icons);
      expect(new Set(list).size, map).toBe(list.length);
    }
  });
});

describe("KindIcon", () => {
  it("hides itself from screen readers when a word sits beside it", () => {
    const { container } = render(<KindLabel icon={CADENCE_ICON.monthly}>Monthly</KindLabel>);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(leadingIcon(container.firstElementChild)).toBe("calendar-1");
    expect(container.firstElementChild).toHaveTextContent("Monthly");
  });

  it("is named, with a tooltip, when it stands alone", () => {
    render(<KindIcon icon={COMING_ICON.late} label="Late" />);
    const img = screen.getByRole("img", { name: "Late" });
    expect(img).toHaveAttribute("title", "Late");
    expect(img.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("takes its colour from a token, or from the words around it", () => {
    const { container } = render(<><KindIcon icon={SCAM_FLAG_ICON} tone="danger" /><KindIcon icon={TRIP_ICON} /></>);
    const [danger, plain] = container.querySelectorAll("svg");
    expect(danger).toHaveClass("text-danger");
    expect(plain.getAttribute("class")).not.toMatch(/text-/);
    expect(plain).toHaveAttribute("stroke", "currentColor");
  });
});
