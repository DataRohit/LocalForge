/**
 * Throwaway TLS certificate.
 *
 * Generates a one-day self-signed certificate for loopback so the browser suite can serve an
 * HTTPS origin, which the secure cookies need. The key lives only in the ignored results directory.
 *
 * @packageDocumentation
 */

import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { generate } from "selfsigned";

const ONE_HOUR_MS = 3_600_000;
const ONE_DAY_MS = 86_400_000;

/**
 * Write a self-signed loopback certificate.
 *
 * Creates the directory, generates a one-day certificate valid for 127.0.0.1 and localhost, and
 * writes key.pem and cert.pem into it.
 *
 * @param directory - Directory to create and fill.
 */
export async function writeCertificate(directory: string): Promise<void> {
  const now = Date.now();
  const pems = await generate([{ name: "commonName", value: "127.0.0.1" }], {
    notBeforeDate: new Date(now - ONE_HOUR_MS),
    notAfterDate: new Date(now + ONE_DAY_MS),
    keySize: 2048,
    extensions: [
      {
        name: "subjectAltName",
        altNames: [
          { type: 7, ip: "127.0.0.1" },
          { type: 2, value: "localhost" },
        ],
      },
    ],
  });
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "key.pem"), pems.private, "utf8");
  await writeFile(path.join(directory, "cert.pem"), pems.cert, "utf8");
}

/**
 * Remove the certificate directory.
 *
 * Deletes the directory with the private key, and does nothing if it is already gone, so
 * cleanup is safe to repeat.
 *
 * @param directory - Directory to delete if present.
 */
export async function removeCertificate(directory: string): Promise<void> {
  await rm(directory, { recursive: true, force: true });
}
