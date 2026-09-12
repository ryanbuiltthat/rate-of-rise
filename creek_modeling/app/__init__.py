"""Creek flood-modeling service (HAOS local add-on).

Phase 2 skeleton: validates the Supervisor API proxy + MQTT wiring and stands up
the persistent-storage layout. The real inference/retrain logic (Phase 4) slots
into `model.py` behind the same interfaces used here.
"""

__version__ = "0.5.0"
