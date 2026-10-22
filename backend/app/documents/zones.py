"""OCR field-zone maps, stored per template version (Section 13).

Coordinates are millimetres from the top-left of the physical page, so the scan agent can
convert them to pixels for any DPI after deskew/crop.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

A4 = (210.0, 297.0)


class Zone(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    pages: Literal["first", "rest", "all"] = "first"


class FieldZones(BaseModel):
    unit: Literal["mm"] = "mm"
    page_width: float = A4[0]
    page_height: float = A4[1]
    fields: dict[str, Zone]

    @model_validator(mode="after")
    def zones_inside_page(self):
        for name, z in self.fields.items():
            if z.x + z.w > self.page_width or z.y + z.h > self.page_height:
                raise ValueError(f"zone '{name}' extends past the page edge")
        return self


# The system footer (see pdf.SYSTEM_CSS) sits in the same place on every page of every
# template: "TOR-2026-000001 | Page 1 of 2 | Ref 1a2b3c4d5e6f". Bulk splitting reads it.
SYSTEM_ZONES = {
    "page_footer": Zone(x=15, y=279, w=180, h=12, pages="all"),
}
