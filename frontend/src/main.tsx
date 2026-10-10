/**
 * Application bootstrap.
 *
 * Removes credentials from the URL fragment before anything renders, then mounts the React tree
 * with the browser router. Every behavior lives in tested modules.
 *
 * @packageDocumentation
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { takeFragmentCredentials } from "./session";
import "./index.css";

const credentials = takeFragmentCredentials(window);
const root = document.getElementById("root");
if (root === null) {
  throw new Error("The document has no root element.");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App credentials={credentials} />
    </BrowserRouter>
  </StrictMode>,
);
