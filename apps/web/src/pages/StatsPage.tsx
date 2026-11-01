import { useApi, type StatResult } from "../api";
import { CardSkeleton, NoResult } from "../components/States";
import { StatCard } from "../stats/StatCard";
import { inCents } from "../stats/money";

/** A page of stat cards, one per id, in the order given. */
export function StatsPage({ ids, intro }: { ids: string[]; intro?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      {intro}
      {ids.map((id) => <OneStat key={id} id={id} />)}
    </div>
  );
}

export function OneStat({ id }: { id: string }) {
  const { data, error, loading, status } = useApi<StatResult>(`/api/results/${id}`);
  const what = id.replace(/-/g, " ");
  // a card-high block while it loads, so the cards below do not jump when it arrives
  if (loading) return <CardSkeleton what={`Loading ${what}`} />;
  if (error || !data) return <NoResult status={status} error={error} what={what} />;
  // G20: a result in dollars (a copy the browser kept from before G20) is as good as
  // none until the stats run again, the way the server answers 404 for one
  if (!inCents(data)) return <NoResult status={404} error={null} what={what} />;
  return <StatCard result={data} />;
}
