/**
 * Application bootstrap.
 *
 * Mounts the React tree into the document root. Every behavior lives in tested modules;
 * this file only connects them to the browser document.
 *
 * @packageDocumentation
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./index.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("The document has no root element.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
