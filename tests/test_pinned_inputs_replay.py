"""Producer post-render provenance survives optional evaluator replay."""

import pytest

from reports_common import SiteError
from reports_presentation_prototype import preserve_pinned_inputs


def test_preserve_exact_producer_stamp_and_links():
    stamp = (
        '<section id="pinned-inputs">\n<h2>Pinned inputs</h2>\n'
        '<p><a href="https://github.com/Living-Proof-Labs/example/tree/abc">'
        "source &amp; revision</a></p>\n</section>\n"
    )
    assert preserve_pinned_inputs("<body>old" + stamp + "</body>", "<body>new</body>") == (
        "<body>new" + stamp + "</body>"
    )
    assert preserve_pinned_inputs("<body>old</body>", "<body>new</body>") == "<body>new</body>"


@pytest.mark.parametrize(
    "original,page",
    [
        ('<section id="pinned-inputs"></section>' * 2, "<body></body>"),
        (
            '<section id="pinned-inputs"></section>',
            '<body><section id="pinned-inputs"></section></body>',
        ),
        ('<section id="pinned-inputs"></section>', "<main>no body</main>"),
    ],
)
def test_ambiguous_stamp_refuses(original, page):
    with pytest.raises(SiteError):
        preserve_pinned_inputs(original, page)
