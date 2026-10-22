"""HTML -> PDF via WeasyPrint, with a system stylesheet every template inherits.

WeasyPrint needs Pango/GObject system libraries. They exist in the Docker image and CI;
on a bare Windows dev machine the import fails and `RendererUnavailable` is raised.
"""

from dataclasses import dataclass


class RendererUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderedPdf:
    content: bytes
    page_count: int


def _css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def system_css(footer_left: str, footer_right: str) -> str:
    """Page size, margins and the footer marker are enforced; templates cannot override them."""
    return f"""
    @page {{
        size: A4 !important;
        margin: 18mm 20mm 22mm 20mm !important;
        @bottom-center {{
            content: {_css_string(footer_left + " | Page ")} counter(page) " of " counter(pages)
                     {_css_string(" | " + footer_right)} !important;
            font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif;
            font-size: 9pt;
            color: #000;
        }}
    }}
    """


def render_pdf(html: str, footer_left: str, footer_right: str) -> RenderedPdf:
    try:
        import weasyprint
        from weasyprint.urls import URLFetcher
    except OSError as e:  # missing Pango/GObject
        raise RendererUnavailable(
            "PDF rendering is unavailable on this machine (WeasyPrint system libraries "
            "missing). Run the API in Docker or on Linux."
        ) from e

    # Only inline data: URIs may load; no file:// or network access from templates (SSRF).
    fetcher = URLFetcher(allowed_protocols={"data"}, timeout=5)
    document = weasyprint.HTML(string=html, url_fetcher=fetcher).render(
        stylesheets=[weasyprint.CSS(string=system_css(footer_left, footer_right))]
    )
    return RenderedPdf(content=document.write_pdf(), page_count=len(document.pages))
