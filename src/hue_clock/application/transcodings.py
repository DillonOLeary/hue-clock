import datetime as dt

from eventsourcing.persistence import Transcoding

from hue_clock.domain.work_day import Provenance


class DateAsISO(Transcoding):
    type = dt.date
    name = "date_iso"

    def encode(self, obj: dt.date) -> str:
        return obj.isoformat()

    def decode(self, data: str) -> dt.date:
        return dt.date.fromisoformat(data)


class ProvenanceAsName(Transcoding):
    type = Provenance
    name = "provenance"

    def encode(self, obj: Provenance) -> str:
        return obj.value

    def decode(self, data: str) -> Provenance:
        return Provenance(data)
