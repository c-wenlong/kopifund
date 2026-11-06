import { useCallback, useEffect, useRef, useState } from "react";

// Audit: the app's rule for an action that cannot be taken back (SPEC.md,
// "Destructive actions"): the first press arms the button, which then says
// what a second press does, and a second press within ARMED_MS acts. Leaving
// the button (blur) or the time running out disarms it. An action that can be
// taken back acts at once and offers Undo in its toast instead.

export const ARMED_MS = 4000;

export function useArmed() {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const disarm = useCallback(() => setArmed(false), []);
  /** The first press arms; a second, while armed, disarms and runs `act`. */
  const press = (act: () => unknown) => {
    if (timer.current) clearTimeout(timer.current);
    if (!armed) {
      setArmed(true);
      timer.current = setTimeout(() => setArmed(false), ARMED_MS);
      return;
    }
    setArmed(false);
    void act();
  };
  return { armed, press, disarm };
}
