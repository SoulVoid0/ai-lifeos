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
import Settings from "./pages/Settings";
import SearchPage from "./pages/SearchPage";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <HashRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="assistant" element={<Assistant />} />
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
