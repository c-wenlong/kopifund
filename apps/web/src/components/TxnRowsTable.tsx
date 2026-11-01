import { formatAmount } from "../money";
import { formatDay } from "../dates";
import { Pager, usePager } from "./Pager";
import { HEAD_ROW, TABLE_BOX, TH } from "./tableKit";

export type TxnRow = { key: string; date: string; amount: number; currency: string; payee: string; text: string; category: string };

// G13: the rows a check would add, or a load added: date, payee, category and
// amount, a page at a time. `list` names the remembered page size. Under 768px
// each row is two lines, payee and amount first, then date and category, so the
// amount is never clipped behind a sideways scroll.
export function TxnRowsTable({ list, rows }: { list: string; rows: TxnRow[] }) {
  const pager = usePager(list, rows.length);
  return (
    <>
      <div className={TABLE_BOX}>
        <table className="num w-full border-collapse text-[12px] max-md:block">
          <thead className="max-md:hidden">
            <tr className={HEAD_ROW}>
              <th scope="col" className={`${TH} px-2`}>Date</th>
              <th scope="col" className={`${TH} px-2`}>Payee</th>
              <th scope="col" className={`${TH} px-2`}>Category</th>
              <th scope="col" className={`${TH} px-2 text-right`}>Amount</th>
            </tr>
          </thead>
          <tbody className="max-md:block">
            {pager.slice(rows).map((r) => (
              <tr key={r.key}
                className="border-t border-line max-md:grid max-md:grid-cols-[minmax(0,1fr)_auto] max-md:gap-x-3 max-md:gap-y-0.5 max-md:px-2 max-md:py-2 max-md:first:border-t-0 max-md:[grid-template-areas:'payee_amount''date_category']">
                <td className="whitespace-nowrap px-2 py-1 text-ink-2 max-md:p-0 max-md:[grid-area:date]">{formatDay(r.date)}</td>
                <td className="px-2 py-1 max-md:break-words max-md:p-0 max-md:text-[13px] max-md:[grid-area:payee]">{r.payee || r.text}</td>
                <td className="px-2 py-1 text-ink-2 max-md:p-0 max-md:text-right max-md:[grid-area:category]">{r.category || "no rule"}</td>
                <td className="whitespace-nowrap px-2 py-1 text-right max-md:p-0 max-md:text-[13px] max-md:[grid-area:amount]">{formatAmount(r.amount, r.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager pager={pager} label="Transactions to add pages" />
    </>
  );
}
