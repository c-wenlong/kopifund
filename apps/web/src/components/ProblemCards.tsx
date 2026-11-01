import { Fragment } from "react";
import { TriangleAlert } from "lucide-react";
import type { ProblemRow } from "../api";
import type { Account } from "../accounts";
import { describeProblem } from "../uploadProblems";
import { BankMark } from "./BankMark";

// G13: one card per problem a check found: a plain headline, which account and
// file it is in, and one next step. The raw code and the server's own sentence
// sit on a small details line. A blocker is in the danger colour; it stops the
// load, and the card says so.
export function ProblemCards({ problems, accounts = [] }: { problems: ProblemRow[]; accounts?: Account[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0">
      {problems.map((p, i) => {
        const v = describeProblem(p, accounts);
        return (
          <li key={`${p.code}-${p.subject}-${i}`}
            className={`flex gap-3 rounded-lg border px-3 py-2.5 ${v.blocker ? "border-danger/40 bg-danger-bg" : "border-warn/40 bg-warn-bg"}`}>
            <TriangleAlert size={16} aria-hidden className={`mt-0.5 shrink-0 ${v.blocker ? "text-danger" : "text-warn"}`} />
            <div className="min-w-0 flex-1">
              <h3 className={`m-0 text-[14px] font-semibold ${v.blocker ? "text-danger" : "text-warn"}`}>{v.headline}</h3>
              {(v.account || v.month || v.files.length > 0) && (
                <p className="m-0 mt-0.5 break-words text-[13px] text-ink">
                  {/* G18: each account named by its folder leads with its bank's mark, as on Transactions;
                      a period's label has no folder, so it stays words alone */}
                  {v.accounts.length > 0
                    ? <>
                        {v.accounts.map((a, j) => (
                          <Fragment key={a.name}>
                            {j > 0 && ", "}
                            <span className="inline-flex items-center gap-1.5 align-middle"><BankMark bank={a.bank} />{a.name}</span>
                          </Fragment>
                        ))}
                        {v.month && `, ${v.month}`}
                      </>
                    : [v.account, v.month].filter(Boolean).join(", ")}
                  {v.files.length > 0 && (
                    <span className="text-ink-2">{v.account || v.month ? ": " : ""}{v.files.slice(0, 3).join(", ")}{v.files.length > 3 ? ` and ${v.files.length - 3} more` : ""}</span>
                  )}
                </p>
              )}
              <p className="m-0 mt-1 text-[13px] text-ink-2">{v.next}</p>
              <p className="m-0 mt-1 break-words text-[12px] text-ink-3">{v.detail}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
