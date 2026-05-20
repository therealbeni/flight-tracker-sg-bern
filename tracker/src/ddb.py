import csv
import io
import sys
import urllib.request
from typing import Optional

from models import AircraftInfo

OGN_DDB_URL = "https://ddb.glidernet.org/download/?t=1"


class DeviceDatabase:
    """OGN device database mapping FLARM addresses to aircraft registrations and models.

    The DDB CSV wraps every value in single quotes and prefixes the header
    line with '#'; both are stripped during parsing.
    """

    def __init__(self):
        self._entries: dict[str, AircraftInfo] = {}

    def load(self, url: str = OGN_DDB_URL, timeout: int = 10) -> int:
        """Fetch and parse the device database. Returns the number of entries loaded."""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                text = response.read().decode("utf-8")

            lines = text.splitlines()
            if lines and lines[0].startswith("#"):
                lines[0] = lines[0][1:]

            reader = csv.DictReader(io.StringIO("\n".join(lines)))
            self._entries = {}
            for row in reader:
                address = row["DEVICE_ID"].strip("'").upper()
                registration = row["REGISTRATION"].strip("'").strip()
                model = row["AIRCRAFT_MODEL"].strip("'").strip()
                if address:
                    self._entries[address] = AircraftInfo(registration=registration, model=model)

            return len(self._entries)
        except (OSError, ValueError, KeyError, csv.Error) as exc:
            print(f"Warning: could not load OGN device database: {exc}", file=sys.stderr)
            return 0

    def lookup(self, address: str) -> Optional[AircraftInfo]:
        return self._entries.get(address.upper())
