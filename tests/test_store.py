"""Store: validity round-trip and old-schema migration."""

import os
import sqlite3
import tempfile
import unittest

from smartpantry.schemas import PriceObservation
from smartpantry.store import Store


class StoreValidityTest(unittest.TestCase):
    def test_validity_round_trip(self):
        store = Store(":memory:")
        store.add_observation(PriceObservation(
            observation_id="o1", product_id="milk-gal", store="Mariano's",
            price=3.79, unit_price=1.0, unit="L", source_type="flyer",
            fetched_at="2026-11-03T12:00:00+00:00", confidence=0.75,
            valid_from="2026-11-02", valid_to="2026-11-08",
            conditions=["loyalty_card"]))
        obs = store.observations_for_product("milk-gal")[0]
        self.assertEqual(obs.valid_to, "2026-11-08")
        self.assertEqual(obs.conditions, ["loyalty_card"])
        store.close()

    def test_old_database_migrates(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE observations (observation_id TEXT PRIMARY KEY,"
                " product_id TEXT, store TEXT, price REAL, unit_price REAL,"
                " unit TEXT, source_type TEXT, fetched_at TEXT,"
                " confidence REAL, verified INTEGER)")
            conn.execute(
                "INSERT INTO observations VALUES ('o1','milk-gal','M',4.29,"
                "1.0,'L','api','2026-11-03T12:00:00+00:00',0.9,1)")
            conn.commit()
            conn.close()
            store = Store(path)
            obs = store.observations_for_product("milk-gal")[0]
            self.assertEqual(obs.valid_to, "")
            self.assertEqual(obs.conditions, [])
            store.close()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
