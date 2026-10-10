/**
 * Route table.
 *
 * Maps every client path in the route map to its screen inside the shell. A path the map does
 * not define falls through to the not-found screen.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";
import { Route, Routes } from "react-router";

import { AccountPage } from "../features/account/AccountPage";
import { NotFound } from "../features/shell/NotFound";
import { Shell } from "../features/shell/Shell";
import { PATHS } from "../session";
import { HomeRedirect, RequireCapability } from "./guards";

/**
 * Render the route table.
 *
 * Every route renders inside the shell, and protected routes sit behind a capability guard.
 * The final catch-all route renders the not-found screen.
 *
 * @returns The routes element.
 */
export function AppRoutes(): JSX.Element {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<HomeRedirect />} />
        <Route
          path={PATHS.account}
          element={
            <RequireCapability capability="account:read">
              <AccountPage />
            </RequireCapability>
          }
        />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
