"""Curated GCP options for the interactive wizard.

Not exhaustive — just sensible, common picks. The wizard always offers a
"custom" escape so any valid region/zone/machine type can still be entered.
"""

from __future__ import annotations

# (region, friendly label). Labels are plain text (questionary styles its own list).
REGIONS: list[tuple[str, str]] = [
    ("us-central1", "us-central1 — Iowa, USA"),
    ("us-east1", "us-east1 — South Carolina, USA"),
    ("us-east4", "us-east4 — N. Virginia, USA"),
    ("us-west1", "us-west1 — Oregon, USA"),
    ("northamerica-northeast1", "northamerica-northeast1 — Montréal, Canada"),
    ("southamerica-east1", "southamerica-east1 — São Paulo, Brazil"),
    ("europe-west1", "europe-west1 — Belgium"),
    ("europe-west2", "europe-west2 — London, UK"),
    ("europe-west3", "europe-west3 — Frankfurt, Germany"),
    ("asia-south1", "asia-south1 — Mumbai, India"),
    ("asia-southeast1", "asia-southeast1 — Singapore"),
    ("asia-northeast1", "asia-northeast1 — Tokyo, Japan"),
    ("australia-southeast1", "australia-southeast1 — Sydney, Australia"),
]

# NOTE: zones are NOT fabricated from suffixes — not every region has a "-a"
# (e.g. us-east1 exposes b/c/d only). Real zones are fetched live via
# discover.zones_for_region(); see the wizard in cli.py.

# (machine_type, friendly label with specs + rough use).
MACHINE_TYPES: list[tuple[str, str]] = [
    ("e2-micro", "e2-micro — 2 vCPU (shared), 1 GB — cheapest, tiny agents"),
    ("e2-small", "e2-small — 2 vCPU (shared), 2 GB — light default"),
    ("e2-medium", "e2-medium — 2 vCPU (shared), 4 GB — comfortable"),
    ("e2-standard-2", "e2-standard-2 — 2 vCPU, 8 GB — general purpose"),
    ("e2-standard-4", "e2-standard-4 — 4 vCPU, 16 GB — heavier workloads"),
    ("n2-standard-2", "n2-standard-2 — 2 vCPU, 8 GB — newer gen"),
    ("c3-standard-4", "c3-standard-4 — 4 vCPU, 16 GB — compute-optimized"),
]
