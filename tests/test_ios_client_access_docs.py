from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ADR_0066 = Path("docs/adr/0066-native-api-opaque-session-boundary.md")
ADR_0067 = Path("docs/adr/0067-native-client-delivery-slice.md")
IOS_ARCHITECTURE = Path("docs/IOS-ARCHITECTURE.md")
IOS_API = Path("docs/IOS-API-V1.md")

EXPECTED_MATRIX = {
    "gallery_guest": ("yes", "one unlocked gallery", "no", "no"),
    "portal_guest": ("yes", "published galleries for its client", "no", "yes"),
    "workspace_guest": (
        "yes",
        "its project's published gallery",
        "its exact project's non-draft documents",
        "yes",
    ),
    "document_guest": (
        "yes",
        "no",
        "one document preview from /client/home; no project collections",
        "no",
    ),
    "studio_owner / unknown": ("not a client principal", "no", "no", "no"),
}


def _client_destination_matrix(text: str) -> dict[str, tuple[str, ...]]:
    """Read the canonical ADR table so capability changes fail in one obvious place."""
    marker = "### Client destination authority"
    assert text.count(marker) == 1, "ADR 0067 must own one linkable destination matrix"
    lines = text.split(marker, 1)[1].splitlines()
    header_indexes = [index for index, line in enumerate(lines) if line.startswith("| Principal |")]
    assert len(header_indexes) == 1, "ADR 0067 must contain one destination table"
    header_index = header_indexes[0]

    rows: dict[str, tuple[str, ...]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = tuple(cell.strip().replace("`", "") for cell in line.strip("|").split("|"))
        assert len(cells) == 5, f"unexpected client destination row: {line}"
        assert cells[0] not in rows, f"duplicate client destination principal: {cells[0]}"
        rows[cells[0]] = cells[1:]
    return rows


def test_adr_0067_keeps_the_exact_client_destination_matrix():
    assert _client_destination_matrix(ADR_0067.read_text()) == EXPECTED_MATRIX


def test_client_authority_docs_link_forward_and_reject_the_old_claims():
    adr_0066 = ADR_0066.read_text()
    architecture = IOS_ARCHITECTURE.read_text()
    api_contract = IOS_API.read_text()

    assert "0067-native-client-delivery-slice.md#client-destination-authority" in adr_0066
    for text in (architecture, api_contract):
        assert "adr/0067-native-client-delivery-slice.md#client-destination-authority" in text

    boundary = "`gallery_guest` visitor, favorite, or original-download authority"
    for text in (adr_0066, architecture, api_contract):
        assert boundary in " ".join(text.split())

    normalized_adr_0066 = " ".join(adr_0066.split())
    normalized_architecture = " ".join(architecture.split())
    normalized_api_contract = " ".join(api_contract.split())
    assert "a workspace does not implicitly unlock its gallery" not in normalized_adr_0066
    assert "it does not automatically unlock linked galleries" not in normalized_architecture
    assert "A portal exchange must not unlock its galleries" not in normalized_api_contract
