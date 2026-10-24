const CACHE_TTL_MS = 5 * 60 * 1000;
const STATIC_READS = new Set(["/api/actions", "/api/examples"]);
const cache = new Map<string, { expiresAt: number; payload: unknown }>();
const inFlight = new Map<string, Promise<unknown>>();

export function cachedStaticApiRead<T>(
  path: string,
  load: () => Promise<T>,
): Promise<T> {
  if (!STATIC_READS.has(path)) return load();

  const cached = cache.get(path);
  if (cached && cached.expiresAt > Date.now())
    return Promise.resolve(structuredClone(cached.payload) as T);
  if (cached) cache.delete(path);

  const pending = inFlight.get(path);
  if (pending) return pending.then((payload) => structuredClone(payload) as T);

  const request = load().then((payload) => {
    cache.set(path, { expiresAt: Date.now() + CACHE_TTL_MS, payload });
    return payload;
  });
  inFlight.set(path, request);
  return request
    .then((payload) => structuredClone(payload) as T)
    .finally(() => inFlight.delete(path));
}
