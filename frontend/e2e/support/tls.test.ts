/**
 * Tests for the throwaway certificate.
 *
 * Confirms a loopback certificate and key are written and removed.
 * The certificate must name both 127.0.0.1 and localhost for the browsers to accept it.
 *
 * @packageDocumentation
 */

import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { X509Certificate } from "node:crypto";

import { describe, expect, it } from "vitest";

import { removeCertificate, writeCertificate } from "./tls.ts";

describe("throwaway certificate", () => {
  it("writes a key and a certificate valid for loopback, then removes them", async () => {
    const base = await mkdtemp(path.join(os.tmpdir(), "lf-tls-"));
    const directory = path.join(base, "tls");
    try {
      await writeCertificate(directory);

      const cert = new X509Certificate(await readFile(path.join(directory, "cert.pem"), "utf8"));
      expect(cert.checkIP("127.0.0.1")).toBe("127.0.0.1");
      expect(cert.checkHost("localhost")).toBe("localhost");
      expect(await readFile(path.join(directory, "key.pem"), "utf8")).toContain("PRIVATE KEY");

      await removeCertificate(directory);
      await expect(stat(directory)).rejects.toThrow();
    } finally {
      await rm(base, { recursive: true, force: true });
    }
  });
});
