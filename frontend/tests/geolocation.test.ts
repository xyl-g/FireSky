import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isValidGeolocationCoordinates,
  requestCurrentLocation,
  type GeolocationProvider,
// Node 26 executes this test through its built-in TypeScript type stripping.
// @ts-expect-error The runtime import intentionally targets the TypeScript source.
} from '../src/lib/utils/geolocation.ts';

function providerFor(
  callback: (success: PositionCallback, failure: PositionErrorCallback, options?: PositionOptions) => void,
): GeolocationProvider {
  return { getCurrentPosition: callback };
}

test('returns browser coordinates without converting valid zero values', async () => {
  const provider = providerFor((success) => {
    success({
      coords: { latitude: 0, longitude: 0, accuracy: 12 },
    } as GeolocationPosition);
  });

  await assert.doesNotReject(async () => {
    const coordinates = await requestCurrentLocation(provider);
    assert.deepEqual(coordinates, { lat: 0, lon: 0, accuracyM: 12 });
  });
});

test('maps permission denial to a typed denied result', async () => {
  const provider = providerFor((_success, failure) => {
    failure({ code: 1, message: 'permission denied' } as GeolocationPositionError);
  });

  await assert.rejects(requestCurrentLocation(provider), (error: unknown) => {
    return error instanceof Error && 'status' in error && error.status === 'denied';
  });
});

test('maps unavailable and timeout independently', async () => {
  const unavailable = providerFor((_success, failure) => {
    failure({ code: 2, message: 'unavailable' } as GeolocationPositionError);
  });
  const timeout = providerFor((_success, failure) => {
    failure({ code: 3, message: 'timeout' } as GeolocationPositionError);
  });

  await assert.rejects(requestCurrentLocation(unavailable), (error: unknown) => {
    return error instanceof Error && 'status' in error && error.status === 'unavailable';
  });
  await assert.rejects(requestCurrentLocation(null), (error: unknown) => {
    return error instanceof Error && 'status' in error && error.status === 'unavailable';
  });
  await assert.rejects(requestCurrentLocation(timeout), (error: unknown) => {
    return error instanceof Error && 'status' in error && error.status === 'timeout';
  });
});

test('rejects malformed browser coordinates before a page can use them', async () => {
  assert.equal(isValidGeolocationCoordinates(90, 180), true);
  assert.equal(isValidGeolocationCoordinates(-90, -180), true);
  assert.equal(isValidGeolocationCoordinates(Number.NaN, 120), false);
  assert.equal(isValidGeolocationCoordinates(30, 181), false);

  const provider = providerFor((success) => {
    success({
      coords: { latitude: Number.NaN, longitude: 120, accuracy: 10 },
    } as GeolocationPosition);
  });

  await assert.rejects(requestCurrentLocation(provider), (error: unknown) => {
    return error instanceof Error && 'status' in error && error.status === 'error';
  });
});
