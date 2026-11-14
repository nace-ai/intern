// Session-wide counter of every fetch the app sends to /nace (Drex chunks and
// retries, NDI uploads, extracts and polls). Never reset during the session.
export const apiCounter = { calls: 0 };

export function countApiCall(): void {
  apiCounter.calls += 1;
}
