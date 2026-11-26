"""Public CAMPDEALZ retail deal service."""

import os
import threading

from datetime import datetime, timezone

from .tools.flipp import (
    FlippDealsConnector,
    configured_merchants,
    configured_postal_codes,
    _active,
    _merchant_key,
)


class LocalDealsService:

    def __init__(self, replay_only=False):

        self.merchants = configured_merchants()
        self.postal_codes = configured_postal_codes()

        self.refresh_hours = max(
            1,
            int(os.environ.get(
                "DEALS_REFRESH_HOURS", "6"
            )),
        )

        self.connectors = [
            FlippDealsConnector(
                postal_code=postal,
                merchants=self.merchants,
                replay_only=replay_only,
            )
            for postal in self.postal_codes
        ]

        self.deals = []
        self.updated_at = None
        self.status = "loading"
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def refresh(self):

        unique = {}
        successful = 0

        for connector in self.connectors:

            try:
                deals = connector.list_deals()
                successful += 1

                for deal in deals:

                    key = (
                        _merchant_key(
                            deal["merchant"]
                        ),
                        deal["name"].lower(),
                        deal["price"],
                        deal.get("valid_to", ""),
                        deal.get("details", ""),
                    )

                    if key not in unique:
                        unique[key] = deal

            except Exception as exc:

                print(
                    f"Deal refresh failed: {exc}",
                    flush=True,
                )

        results = sorted(
            unique.values(),
            key=lambda item: (
                -(item.get(
                    "discount_percent"
                ) or 0),
                item["price"],
            ),
        )

        with self.lock:

            if successful:
                self.deals = results

                self.updated_at = (
                    datetime.now(timezone.utc)
                    .isoformat()
                )

                self.status = (
                    "ready"
                    if successful == len(
                        self.connectors
                    )
                    else "partial"
                )

            else:
                self.status = "unavailable"

        print(
            f"CAMPDEALZ: {len(results)} deals found",
            flush=True,
        )

    def _worker(self):

        while not self.stop_event.is_set():

            try:
                self.refresh()

            except Exception as exc:
                print(
                    f"Refresh error: {exc}",
                    flush=True,
                )

            if self.stop_event.wait(
                self.refresh_hours * 3600
            ):
                break

    def start(self):

        thread = threading.Thread(
            target=self._worker,
            daemon=True,
        )

        thread.start()

    def close(self):
        self.stop_event.set()

    def query(
        self,
        merchant="",
        search="",
        min_discount=0,
        max_price=None,
    ):

        with self.lock:
            deals = list(self.deals)
            status = self.status
            updated_at = self.updated_at

        results = []

        for deal in deals:

            if not _active(deal):
                continue

            if merchant and (
                _merchant_key(
                    deal["merchant"]
                ) != _merchant_key(merchant)
            ):
                continue

            searchable = (
                deal["name"] + " "
                + deal["merchant"] + " "
                + deal.get("details", "")
            ).lower()

            if search and (
                search.lower() not in searchable
            ):
                continue

            if min_discount and (
                deal.get("discount_percent") or 0
            ) < min_discount:
                continue

            if (
                max_price is not None
                and deal["price"] > max_price
            ):
                continue

            results.append(deal)

        return {
            "status": status,
            "count": len(results),
            "updated_at": updated_at,
            "postal_codes": self.postal_codes,
            "merchants": self.merchants,
            "deals": results,
            "location": "Camp Creek Parkway",
            "branch_verified": False,
        }