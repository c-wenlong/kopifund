// The one Node function a test reads a source file with (src/foundations.css.test.ts),
// typed here rather than pulling in @types/node for the whole app.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
