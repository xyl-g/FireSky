import test from "node:test";
import assert from "node:assert/strict";
import { drawnRegionBounds, splitRegionBounds, validCustomRegionBounds, validRegionBounds } from "./map-region.ts";

test("drawn region expands outward to the same quarter-degree grid as Swift", () => {
  assert.deepEqual(drawnRegionBounds({ west: 120.12, south: 30.12, east: 120.51, north: 30.51 }), {
    west: 120, south: 30, east: 120.75, north: 30.75,
  });
  assert.deepEqual(drawnRegionBounds({ west: -.12, south: -.12, east: 0, north: 0 }), {
    west: -.25, south: -.25, east: 0, north: 0,
  });
  assert.deepEqual(drawnRegionBounds({ west: -180, south: -89, east: 180, north: 89 }), {
    west: -180, south: -85, east: 180, north: 85,
  });
});

test("drawn region rejects degenerate, out-of-range and non-finite bounds", () => {
  for (const bounds of [
    { west: 0, south: 0, east: 0, north: 1 },
    { west: 1, south: 1, east: 0, north: 0 },
    { west: 180, south: 0, east: 181, north: 1 },
    { west: NaN, south: 0, east: 1, north: 1 },
    { west: 0, south: 85.1, east: 1, north: 86 },
  ]) assert.equal(drawnRegionBounds(bounds), null);
});

test("region validation preserves zero-valued edges and rejects invalid previews", () => {
  assert.equal(validRegionBounds({ west: 0, south: 0, east: .25, north: .25 }), true);
  assert.equal(validRegionBounds(null), false);
  assert.equal(validRegionBounds({ west: -181, south: 0, east: 1, north: 1 }), false);
  assert.equal(validRegionBounds({ west: 181, south: 0, east: -170, north: 1 }), false);
  assert.equal(validRegionBounds({ west: 170, south: 0, east: -181, north: 1 }), false);
  assert.equal(validRegionBounds({ west: 0, south: 0, east: 1, north: Infinity }), false);
  assert.equal(validRegionBounds({ west: 0, south: -86, east: 1, north: 0 }), false);
  assert.equal(validCustomRegionBounds({ west: 0, south: -90, east: 1, north: 90 }), true);
  assert.equal(validCustomRegionBounds({ west: 0, south: 0, east: 1, north: 0 }), false);
});

test("date-line bounds remain wrapped in the contract and split into narrow map rectangles", () => {
  const bounds = { west: 170, east: -170, south: -10, north: 10 };
  assert.equal(validRegionBounds(bounds), true);
  assert.deepEqual(drawnRegionBounds(bounds), bounds);
  assert.deepEqual(splitRegionBounds(bounds), [
    { west: 170, south: -10, east: 180, north: 10 },
    { west: -180, south: -10, east: -170, north: 10 },
  ]);

  const serialized = JSON.parse(JSON.stringify({ custom_bounds: bounds })).custom_bounds;
  assert.deepEqual(serialized, bounds);
  assert.deepEqual(Object.keys(serialized), ["west", "east", "south", "north"]);
  assert.equal(validRegionBounds({ ...bounds, east: 170 }), false);
});

test("drawn wrapped bounds expand outward without sorting west and east", () => {
  assert.deepEqual(drawnRegionBounds({ west: 170.12, east: -170.12, south: -10.12, north: 10.12 }), {
    west: 170, east: -170, south: -10.25, north: 10.25,
  });
});
