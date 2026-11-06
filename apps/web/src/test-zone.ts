import { afterEach } from "vitest";

// Audit: a test that sets the device's time zone. Node reads TZ again whenever
// it is set, so a test can move between zones; each test file that calls
// `zoneSwitch()` gets the zone it started with back after every test.
const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process.env;

export function zoneSwitch(): (tz: string) => void {
  const start = env.TZ;
  afterEach(() => {
    if (start === undefined) delete env.TZ;
    else env.TZ = start;
  });
  return (tz) => {
    env.TZ = tz;
  };
}
