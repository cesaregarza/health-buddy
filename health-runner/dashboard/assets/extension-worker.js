/* Finite API-1 view bridge. Owner-reviewed code is trusted, not sandboxed. */
'use strict';
self.onmessage = async event => {
  try {
    const input = event.data;
    if (!input || typeof input.module !== 'string' ||
        !/^\/v1\/extensions\/[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\/view\.js\?/.test(input.module) ||
        typeof input.entrypoint !== 'string' || !/^[a-z_]+$/.test(input.entrypoint)) {
      throw new Error('invalid_input');
    }
    const url = new URL(input.module, self.location.origin);
    if (url.origin !== self.location.origin) throw new Error('invalid_origin');
    const loaded = await import(url.href);
    const result = await loaded[input.entrypoint]({metric:input.metric,config:input.config});
    const encoded = JSON.stringify(result);
    if (typeof encoded !== 'string' || encoded.length > 8192) throw new Error('invalid_output');
    self.postMessage({ok: true, view: JSON.parse(encoded)});
  } catch (_) {
    self.postMessage({ok: false});
  }
};
