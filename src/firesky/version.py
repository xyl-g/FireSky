"""Firesky engine version identity.

The physics follows the original regional scoring contract this engine was
born from; the
algorithm string records that lineage so caches produced by the two engines
can be told apart. Bump on any scoring-semantics change.
"""

FIRESKY_ALGORITHM_VERSION = "2026.11.04.87"
# Kept for the ported modules that reference the national contract string.
NATIONAL_FORECAST_ALGORITHM_VERSION = FIRESKY_ALGORITHM_VERSION
FORECAST_ALGORITHM_VERSION = FIRESKY_ALGORITHM_VERSION
# The minimal engine does not ingest the satellite nowcast path, but the
# ported models module references the schema constants.
NOWCAST_ALGORITHM_VERSION = "satellite-nowcast-2026.11.04.87-hsd-multi-observation-aerosol-ground-touchdown-v1"
NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION = 4
