import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Assistant from "./pages/Assistant";
import Memory from "./pages/Memory";
import Files from "./pages/Files";
import Calendar from "./pages/Calendar";
import ActivityLog from "./pages/ActivityLog";
import Computer from "./pages/Computer";
import Settings from "./pages/Settings";
import SearchPage from "./pages/SearchPage";
import ControlCenter from "./pages/ControlCenter";
import World from "./pages/World";
import Automations from "./pages/Automations";
import Projects from "./pages/Projects";
import Gallery from "./pages/Gallery";
import Wallpaper from "./pages/Wallpaper";
import Arena from "./pages/Arena";
import Council from "./pages/Council";
import Stats from "./pages/Stats";
import Tools, { MiniAppWindow } from "./pages/Tools";
import Learn from "./pages/Learn";
import Stories from "./pages/Stories";
import Doctor from "./pages/Doctor";
import Pet from "./pages/Pet";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <HashRouter>
        <Routes>
          <Route path="wallpaper" element={<Wallpaper />} />
          <Route path="pet" element={<Pet />} />
          <Route path="miniapp/:id" element={<MiniAppWindow />} />
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="computer" element={<Computer />} />
            <Route path="crew" element={<ControlCenter />} />
            <Route path="station" element={<World />} />
            <Route path="assistant" element={<Assistant />} />
            <Route path="automations" element={<Automations />} />
            <Route path="projects" element={<Projects />} />
            <Route path="gallery" element={<Gallery />} />
            <Route path="arena" element={<Arena />} />
            <Route path="council" element={<Council />} />
            <Route path="stats" element={<Stats />} />
            <Route path="tools" element={<Tools />} />
            <Route path="learn" element={<Learn />} />
            <Route path="stories" element={<Stories />} />
            <Route path="doctor" element={<Doctor />} />
            <Route path="memory" element={<Memory />} />
            <Route path="files" element={<Files />} />
            <Route path="calendar" element={<Calendar />} />
            <Route path="activity" element={<ActivityLog />} />
            <Route path="settings" element={<Settings />} />
            <Route path="search" element={<SearchPage />} />
          </Route>
        </Routes>
      </HashRouter>
    </ToastProvider>
  </React.StrictMode>,
);
