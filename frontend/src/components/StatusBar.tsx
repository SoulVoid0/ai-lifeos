import { useEffect, useState } from "react";
import { Battery, BatteryCharging, Cpu, Maximize2, MemoryStick, Minimize2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { isDesktop } from "../lib/desktop";
import { getCurrentWindow } from "@tauri-apps/api/window";

/** The shell's header widgets: live CPU/memory/battery, a clock, and full-screen (shell) mode with F11. */
export function StatusBar() {
  const navigate = useNavigate();
  const st = useAsync(api.systemStatus, [], 10000);
  const [now, setNow] = useState(new Date());
  const [full, setFull] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15000);
    const onFs = () => setFull(isDesktop ? desktopFull : !!document.fullscreenElement);
    const onKey = (e: KeyboardEvent) => { if (e.key === "F11") { e.preventDefault(); toggleFullscreen(); } };
    document.addEventListener("fullscreenchange", onFs);
    window.addEventListener("lifeos:fullscreen", onFs);
    window.addEventListener("keydown", onKey);
    return () => { clearInterval(t); document.removeEventListener("fullscreenchange", onFs); window.removeEventListener("lifeos:fullscreen", onFs); window.removeEventListener("keydown", onKey); };
  }, []);

  const s = st.data;
  return (
    <div className="flex items-center gap-3 text-xs text-muted">
      {s && (
        <button className="hidden items-center gap-3 hover:text-text md:flex" onClick={() => navigate("/computer")} title="Open Computer">
          <span className="flex items-center gap-1"><Cpu size={13} />{Math.round(s.cpu_percent)}%</span>
          <span className="flex items-center gap-1"><MemoryStick size={13} />{Math.round(s.memory.percent)}%</span>
          {s.battery && (
            <span className="flex items-center gap-1">
              {s.battery.plugged_in ? <BatteryCharging size={13} /> : <Battery size={13} />}{s.battery.percent}%
            </span>
          )}
        </button>
      )}
      <span className="tabular-nums text-text">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      <button className="btn-ghost px-1.5" onClick={toggleFullscreen} title={full ? "Leave full screen (F11)" : "Full screen shell mode (F11)"}>
        {full ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
      </button>
    </div>
  );
}

let desktopFull = false;

export function toggleFullscreen() {
  if (isDesktop) {
    // The desktop window itself goes full screen (taskbar hidden), like a shell.
    desktopFull = !desktopFull;
    getCurrentWindow().setFullscreen(desktopFull).catch(() => {});
    window.dispatchEvent(new Event("lifeos:fullscreen"));
    return;
  }
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else document.documentElement.requestFullscreen().catch(() => {});
}
