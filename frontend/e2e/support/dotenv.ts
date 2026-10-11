/**
 * Environment file parser.
 *
 * Reads the generated dotenv files into plain objects so the end-to-end runner can pass the testing
 * values to child processes. It never logs a value and never reads the development file.
 *
 * @packageDocumentation
 */

/**
 * Parse dotenv text.
 *
 * Skips blank lines, comments, and lines without an equals sign, splits each remaining line at
 * the first equals sign, and removes one pair of matching surrounding quotes from the value.
 *
 * @param text - Contents of an environment file.
 * @returns The key and value pairs in file order.
 */
export function parseDotenv(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (line.trim() === "" || line.trimStart().startsWith("#") || separator === -1) {
      continue;
    }
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    const quote = value.at(0);
    if (value.length >= 2 && (quote === '"' || quote === "'") && value.endsWith(quote)) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}
