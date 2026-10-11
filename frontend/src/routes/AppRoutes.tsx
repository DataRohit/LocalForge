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
import { ActivatePage } from "../features/auth/ActivatePage";
import { RegisterPage } from "../features/auth/RegisterPage";
import { ResendActivationPage } from "../features/auth/ResendActivationPage";
import { SignInPage } from "../features/auth/SignInPage";
import { NotFound } from "../features/shell/NotFound";
import { Shell } from "../features/shell/Shell";
import { PATHS } from "../session";
import { HomeRedirect, PublicOnly, RequireCapability } from "./guards";

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
          path={PATHS.signIn}
          element={
            <PublicOnly>
              <SignInPage />
            </PublicOnly>
          }
        />
        <Route
          path={PATHS.register}
          element={
            <PublicOnly>
              <RegisterPage />
            </PublicOnly>
          }
        />
        <Route path={PATHS.activate} element={<ActivatePage />} />
        <Route path={PATHS.resendActivation} element={<ResendActivationPage />} />
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
