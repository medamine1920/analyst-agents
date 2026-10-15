import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import { applyPalette } from "./lib/signs";

applyPalette(); // gains and losses in the colours chosen under Settings > Display
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
