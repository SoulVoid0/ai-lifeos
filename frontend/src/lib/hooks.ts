import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";

const keyOf = (v: unknown) => {
  try {
    return JSON.stringify(v);
  } catch {
    return undefined;
  }
};

/**
 * Load async data, with reload() and optional polling. Polls wait for the previous request to finish, pause while the
 * window is hidden (and catch up when it's shown again), and keep the old object when nothing changed, so a poll that
 * returns the same data doesn't re-render anything.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number) {
  const [data, setDataState] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const last = useRef<string | undefined>();

  const reload = useCallback(async () => {
    try {
      const value = await fnRef.current();
      const key = keyOf(value);
      if (key === undefined || key !== last.current) {
        last.current = key;
        setDataState(value);
      }
      setError(undefined);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const setData = useCallback((v: SetStateAction<T | undefined>) => {
    last.current = undefined;
    setDataState(v);
  }, []);

  useEffect(() => {
    setLoading(true);
    let stop = false, busy = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      clearTimeout(timer);
      busy = true;
      await reload();
      busy = false;
      if (!stop && pollMs) timer = setTimeout(poll, pollMs);
    };
    const poll = () => { if (!document.hidden) tick(); };
    const onVisible = () => { if (pollMs && !stop && !busy && !document.hidden) tick(); };
    tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stop = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}

/** A localStorage-backed setting for this device (falls back to the default when storage is unavailable). */
export function useLocal<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (localStorage.getItem(key) as T | null) ?? fallback;
    } catch {
      return fallback;
    }
  });
  const set = useCallback((v: T) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      /* private mode: keep it for this session */
    }
  }, [key]);
  return [value, set];
}
