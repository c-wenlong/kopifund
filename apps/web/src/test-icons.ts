// G18: for the page tests. The lucide name of the kind icon that leads an
// element (its first child, a KindIcon from web/src/icons.tsx), such as
// "circle-check"; null when the element does not open with one.
export function leadingIcon(el: Element | null | undefined): string | null {
  const first = el?.firstElementChild;
  if (!first?.hasAttribute("data-kind-icon")) return null;
  const svg = first.tagName.toLowerCase() === "svg" ? first : first.querySelector("svg");
  // lucide writes the name twice, "lucide-calendar1 lucide-calendar-1"; the second is the icon's own file name
  const name = (svg?.getAttribute("class") ?? "").split(/\s+/).filter((c) => c.startsWith("lucide-")).pop();
  return name ? name.slice("lucide-".length) : null;
}
