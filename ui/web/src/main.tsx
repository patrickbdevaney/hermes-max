import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { applySettings } from "./lib/settings";

applySettings(); // density / forced reduced-motion before first paint

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
