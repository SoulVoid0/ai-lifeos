// Desktop-app (Tauri) features. In a normal browser tab these are no-ops.
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export const isDesktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export interface UpdateInfo { version: string; current: string; notes: string | null }

export const appVersion = () => (isDesktop ? invoke<string>("app_version") : Promise.resolve(""));
export const checkUpdate = () => invoke<UpdateInfo | null>("check_update");
export const installUpdate = () => invoke<void>("install_update");

export function onDesktopEvent<T>(name: string, fn: (payload: T) => void): () => void {
  if (!isDesktop) return () => {};
  let off: UnlistenFn | undefined;
  let gone = false;
  listen<T>(name, (e) => fn(e.payload)).then((u) => (gone ? u() : (off = u)));
  return () => { gone = true; off?.(); };
}
