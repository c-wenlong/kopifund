// G26: the note widget: a line of text kept in the widget's options, bound to
// no query. The board's other widgets are charts under dashboard/widgets/.
import type { WidgetProps } from "./registry";

/** `note`: a line of text kept in the widget's options. */
export function TextWidget({ widget }: WidgetProps) {
  const text = typeof widget.options?.text === "string" ? widget.options.text : "";
  return <p className="m-0 whitespace-pre-wrap text-[13px] text-ink-2">{text || "A note for this board."}</p>;
}
