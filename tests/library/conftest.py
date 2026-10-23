from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _page(writer: PdfWriter, text: str) -> None:
    page = writer.add_blank_page(width=612, height=792)
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )


def make_text_pdf(*lines: str) -> bytes:
    writer = PdfWriter()
    for line in lines:
        _page(writer, line)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()
