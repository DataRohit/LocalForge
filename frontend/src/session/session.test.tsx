/**
 * Tests for the session provider and hooks.
 *
 * Drives every session state through the mock backend: loading, signed in with capabilities,
 * signed out, errors, and recovery. Also covers the actions and the cache clearing on session loss.
 *
 * @packageDocumentation
 */

import { act, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Component, useState } from "react";
import type { JSX, ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { envelope, fail, profile, sequence, server, succeed } from "../../test/backend";
import { takeConsoleCalls } from "../../test/console";
import { setCookies } from "../../test/cookies";
import { renderWithSession } from "../../test/render";
import { operations } from "../api/generated/operations";
import { notifyUnauthorized } from "../api/unauthorized";
import { useFragmentCredentials } from "./FragmentCredentialsContext";
import { useSession, useSessionActions, useSignedInSession, useSignInNotice } from "./useSession";

function Probe(): JSX.Element {
  const session = useSession();
  const actions = useSessionActions();
  const notice = useSignInNotice();
  const [result, setResult] = useState("");
  return (
    <div>
      <p>state: {session.status}</p>
      <p>notice: {notice ?? "none"}</p>
      {session.status === "signedIn" ? (
        <>
          <p>user: {session.profile.username}</p>
          <p>can read: {String(session.can("account:read"))}</p>
          <p>can delete: {String(session.can("account:delete"))}</p>
        </>
      ) : null}
      {session.status === "error" ? (
        <>
          <p>problem: {session.problem.kind}</p>
          <button onClick={session.retry}>retry</button>
        </>
      ) : null}
      <p>result: {result}</p>
      <button
        onClick={() => {
          void actions.end().then((problem) => {
            setResult(problem === null ? "ended" : problem.kind);
          });
        }}
      >
        end
      </button>
      <button
        onClick={() => {
          actions.establish(profile({ username: "grace" }));
        }}
      >
        establish
      </button>
      <button
        onClick={() => {
          actions.drop();
        }}
      >
        drop
      </button>
      <button
        onClick={() => {
          actions.drop("password-changed");
        }}
      >
        drop with notice
      </button>
      <button
        onClick={() => {
          void actions.refresh();
        }}
      >
        refresh
      </button>
    </div>
  );
}

class Boundary extends Component<{ children: ReactNode }, { message: string | null }> {
  override state = { message: null as string | null };

  static getDerivedStateFromError(error: Error): { message: string } {
    return { message: error.message };
  }

  override render(): ReactNode {
    return this.state.message === null ? this.props.children : <p>{this.state.message}</p>;
  }
}

function signedOutBackend(): void {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
}

describe("SessionProvider", () => {
  it("is loading first and then signed in with the granted capabilities", async () => {
    succeed("user_profile_retrieve", 200, profile({ capabilities: ["account:read"] }));
    renderWithSession(<Probe />);

    expect(screen.getByText("state: loading")).toBeInTheDocument();

    expect(await screen.findByText("state: signedIn")).toBeInTheDocument();
    expect(screen.getByText("user: ada")).toBeInTheDocument();
    expect(screen.getByText("can read: true")).toBeInTheDocument();
    expect(screen.getByText("can delete: false")).toBeInTheDocument();
  });

  it("is signed out when the profile is refused after a failed refresh", async () => {
    signedOutBackend();
    renderWithSession(<Probe />);

    expect(await screen.findByText("state: signedOut")).toBeInTheDocument();
  });

  it("recovers a session whose access cookie expired", async () => {
    setCookies("__Host-lf-csrf=token");
    sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
      { status: 200, body: profile() },
    ]);
    succeed("browser_refresh", 204);
    renderWithSession(<Probe />);

    expect(await screen.findByText("state: signedIn")).toBeInTheDocument();
  });

  it("reports an offline error and retries on request", async () => {
    server.use(http.get(operations.user_profile_retrieve.path, () => HttpResponse.error()));
    const { user } = renderWithSession(<Probe />);

    expect(await screen.findByText("state: error")).toBeInTheDocument();
    expect(screen.getByText("problem: offline")).toBeInTheDocument();

    server.resetHandlers();
    succeed("user_profile_retrieve", 200, profile());
    await user.click(screen.getByRole("button", { name: "retry" }));

    expect(await screen.findByText("state: signedIn")).toBeInTheDocument();
  });

  it("reports a server failure as an error state", async () => {
    fail("user_profile_retrieve", 503, envelope("service_unavailable", "Down."));
    renderWithSession(<Probe />);

    expect(await screen.findByText("problem: server")).toBeInTheDocument();
  });

  it("establishes a session from a profile without a request", async () => {
    signedOutBackend();
    const { user } = renderWithSession(<Probe />);
    await screen.findByText("state: signedOut");

    await user.click(screen.getByRole("button", { name: "establish" }));

    expect(await screen.findByText("user: grace")).toBeInTheDocument();
  });

  it("ends the session through logout and clears every cached query", async () => {
    setCookies("__Host-lf-csrf=token");
    succeed("user_profile_retrieve", 200, profile());
    succeed("browser_logout", 204);
    const { user, client } = renderWithSession(<Probe />);
    await screen.findByText("state: signedIn");
    client.setQueryData(["other"], "cached");

    await user.click(screen.getByRole("button", { name: "end" }));

    expect(await screen.findByText("state: signedOut")).toBeInTheDocument();
    expect(await screen.findByText("result: ended")).toBeInTheDocument();
    expect(client.getQueryData(["other"])).toBeUndefined();
  });

  it("keeps the session when logout fails and reports the problem", async () => {
    setCookies("__Host-lf-csrf=token");
    succeed("user_profile_retrieve", 200, profile());
    server.use(http.post(operations.browser_logout.path, () => HttpResponse.error()));
    const { user } = renderWithSession(<Probe />);
    await screen.findByText("state: signedIn");

    await user.click(screen.getByRole("button", { name: "end" }));

    expect(await screen.findByText("result: offline")).toBeInTheDocument();
    expect(screen.getByText("state: signedIn")).toBeInTheDocument();
  });

  it("drops the session locally without a request", async () => {
    succeed("user_profile_retrieve", 200, profile());
    const { user } = renderWithSession(<Probe />);
    await screen.findByText("state: signedIn");

    await user.click(screen.getByRole("button", { name: "drop" }));

    expect(await screen.findByText("state: signedOut")).toBeInTheDocument();
  });

  it("keeps a sign-in notice after a drop and clears it when a session is established", async () => {
    succeed("user_profile_retrieve", 200, profile());
    const { user } = renderWithSession(<Probe />);
    await screen.findByText("state: signedIn");
    expect(screen.getByText("notice: none")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "drop with notice" }));

    expect(await screen.findByText("notice: password-changed")).toBeInTheDocument();
    expect(screen.getByText("state: signedOut")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "establish" }));

    expect(await screen.findByText("notice: none")).toBeInTheDocument();
  });

  it("refetches the profile when asked, so a change the server made shows up", async () => {
    const calls = sequence("user_profile_retrieve", [
      { status: 200, body: profile() },
      { status: 200, body: profile({ username: "renamed" }) },
    ]);
    const { user } = renderWithSession(<Probe />);
    expect(await screen.findByText("user: ada")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "refresh" }));

    expect(await screen.findByText("user: renamed")).toBeInTheDocument();
    expect(calls()).toBe(2);
  });

  it("does not announce a lost session to a visitor who never had one", async () => {
    signedOutBackend();
    renderWithSession(<Probe />);
    await screen.findByText("state: signedOut");

    act(() => {
      notifyUnauthorized();
    });

    expect(screen.getByText("notice: none")).toBeInTheDocument();
  });

  it("signs out everywhere when the API client announces session loss", async () => {
    succeed("user_profile_retrieve", 200, profile());
    const { client } = renderWithSession(<Probe />);
    await screen.findByText("state: signedIn");
    client.setQueryData(["other"], "cached");

    act(() => {
      notifyUnauthorized();
    });

    expect(await screen.findByText("state: signedOut")).toBeInTheDocument();
    expect(client.getQueryData(["other"])).toBeUndefined();
    expect(screen.getByText("notice: session-ended")).toBeInTheDocument();
  });
});

describe("session hooks outside their providers", () => {
  it("useSession and useSessionActions throw without a SessionProvider", () => {
    function Bare(): JSX.Element {
      useSession();
      return <p />;
    }

    expect(() => render(<Bare />)).toThrow("SessionProvider");
  });

  it("useSignedInSession throws unless the session is signed in", async () => {
    signedOutBackend();
    function Needs(): JSX.Element {
      return <p>{useSignedInSession().profile.username}</p>;
    }
    function Gate(): JSX.Element {
      return useSession().status === "loading" ? <p>waiting</p> : <Needs />;
    }

    renderWithSession(
      <Boundary>
        <Gate />
      </Boundary>,
    );

    expect(screen.getByText("waiting")).toBeInTheDocument();
    expect(await screen.findByText(/needs a signed-in session/)).toBeInTheDocument();
    expect(takeConsoleCalls().length).toBeGreaterThan(0);
  });

  it("useSignedInSession returns the profile when signed in", async () => {
    succeed("user_profile_retrieve", 200, profile());
    function Needs(): JSX.Element {
      return <p>name: {useSignedInSession().profile.username}</p>;
    }
    function Gate(): JSX.Element {
      return useSession().status === "loading" ? <p>waiting</p> : <Needs />;
    }

    renderWithSession(<Gate />);

    expect(await screen.findByText("name: ada")).toBeInTheDocument();
  });
});

describe("fragment credentials context", () => {
  function Reader(): JSX.Element {
    const { credentials, consume } = useFragmentCredentials();
    return (
      <div>
        <p>{credentials === null ? "none" : `${credentials.account}:${credentials.token}`}</p>
        <button onClick={consume}>use</button>
      </div>
    );
  }

  it("provides the credentials and forgets them once consumed", async () => {
    succeed("user_profile_retrieve", 200, profile());
    const { user } = renderWithSession(<Reader />, {
      credentials: { account: "a1", token: "t1" },
    });
    expect(screen.getByText("a1:t1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "use" }));

    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("throws outside the provider", () => {
    expect(() => render(<Reader />)).toThrow("FragmentCredentialsProvider");
  });
});
