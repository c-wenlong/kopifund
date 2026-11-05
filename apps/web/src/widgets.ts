// G26: a widget's data, one WidgetQuery at a time (server/finance/dashboard.py).
// The GET asks `/api/dashboard/query?q=<json>`, which serve.py lists in CACHED,
// so a widget keeps its last answer, asks again with its ETag, and reloads on
// every write, exactly like a page (api.ts). A bad query answers 4xx and the
// widget shows the error, never a blank chart.
import { useApi, postJSON, type Loaded } from "./api";
import type { ScenarioPreview, WidgetAnswer, WidgetQuery } from "./types.gen";

export function widgetQueryUrl(query: WidgetQuery): string {
  return `/api/dashboard/query?q=${encodeURIComponent(JSON.stringify(query))}`;
}

/** A widget's answer to its query: the kept one at once, then the server's. */
export function useWidgetData(query: WidgetQuery | null): Loaded<WidgetAnswer> {
  return useApi<WidgetAnswer>(query ? widgetQueryUrl(query) : null);
}

/** The same resolution, posted: for callers outside the view cache. Writes nothing. */
export function postWidgetQuery(query: WidgetQuery): Promise<WidgetAnswer> {
  return postJSON<WidgetAnswer>("/api/dashboard/query", JSON.stringify(query));
}

/** The query's series drawn `months` ahead on its trailing monthly average. An estimate, never saved. */
export function previewScenario(body: {
  query: WidgetQuery;
  months?: number;
  delta_minor?: number;
}): Promise<ScenarioPreview> {
  return postJSON<ScenarioPreview>("/api/dashboard/scenario/preview", JSON.stringify(body));
}
