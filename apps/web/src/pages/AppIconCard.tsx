// The "App icon" card on Settings: the icon sets in app-icons/sets.json, one
// picked (POST /api/app-icon). The pick moves the tab's icon and the logo
// beside the app's name at once, and the Dock icon in the Mac app; the
// iPhone app picks its own in its Settings (SPEC.md, "App icon").
import { useState } from "react";
import { useApi } from "../api";
import { Card } from "../components/Card";
import { iconFile, pickAppIcon } from "../components/appIcon";
import type { AppIconPage } from "../types.gen";

export function AppIconCard() {
  const page = useApi<AppIconPage>("/api/app-icon");
  const [picked, setPicked] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // an answer without the sets (an older server, a test's stand-in) is no answer to draw
  const data = Array.isArray(page.data?.sets) ? page.data : null;
  if (!data) return null;
  const chosen = picked ?? data.chosen;

  async function pick(name: string) {
    const was = chosen;
    setPicked(name);
    setProblem(null);
    try {
      setPicked((await pickAppIcon(name)).chosen);
    } catch (e) {
      setPicked(was);
      setProblem((e as Error).message);
    }
  }

  return (
    <Card title="App icon">
      <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
        <legend className="sr-only">App icon</legend>
        {data.sets.map((s) => (
          <label key={s.name} className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-[14px] hover:bg-hover">
            <input type="radio" name="app-icon" value={s.name} checked={chosen === s.name}
              onChange={() => void pick(s.name)} />
            <img src={iconFile(s.name, "icon-picker-120.png")} alt="" className="size-10 shrink-0 rounded-[9px]" />
            <span className="flex min-w-0 flex-col">
              <span>{s.label}{s.name === data.default && <span className="text-ink-3"> (default)</span>}</span>
              <span className="text-[13px] text-ink-3">{s.about}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {problem && <p className="m-0 mt-2 text-[13px] text-warn">{problem}</p>}
      <p className="m-0 mt-2 text-[13px] text-ink-3">
        Changes the tab icon and the logo here, and the Dock icon in the Mac app. A home screen app keeps the
        icon it was added with, and the iPhone app picks its own in its Settings.
      </p>
    </Card>
  );
}
