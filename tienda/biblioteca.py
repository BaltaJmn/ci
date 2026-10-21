#!/usr/bin/env python3
"""App Asset Library of App Store Connect: where the images of In-App Events (and, from here on, of any
other surface) live. The set-based resources (appScreenshotSets, appEventScreenshots) are deprecated.

The API does NOT take new images: reserving, PUTting the parts and committing with `uploaded: true`
ends FAILED with INVALID_ASSET_FILE_FORMAT, for any PNG, even one that is valid in the listing
(chroma, 21-10-2026; `sourceFileChecksum` is not an attribute here). The web of App Store Connect takes
them: upload each image once there (Claude in Chrome, `file_upload` on the event's file input), wait
for the asset to reach PREPARE_FOR_SUBMISSION (~2 min), and place it on the other languages from here:

    asset = imagen_de(placement_id)                    # asset id of what the web placed on en-US
    colocar_evento(asset, localization_id, "card")     # once per event localization

Docs: developer.apple.com/documentation/appstoreconnectapi/migrating-to-the-app-asset-library
"""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import appstore as a

EVENTO = {"card": "EVENT_CARD_ASSET", "details": "EVENT_DETAILS_PAGE_ASSET"}


def imagen_de(placement_id):
    """The asset id behind a placement (the relationship has no endpoint of its own, `include` gives it)."""
    return a.api("GET", f"/v1/appAssetLibraryPlacements/{placement_id}?include=image")["data"]["relationships"]["image"]["data"]["id"]


def colocar_evento(asset_id, localization_id, tipo):
    """One asset on one event localization (tipo: card o details). Replaces the one already there."""
    for p in a.get_all(f"/v1/appEventLocalizations/{localization_id}/placements"):
        if p["attributes"]["placementType"] == EVENTO[tipo]:
            a.api("DELETE", f"/v1/appAssetLibraryPlacements/{p['id']}")
    a.api("POST", "/v1/appAssetLibraryPlacements", {"data": {"type": "appAssetLibraryPlacements",
        "attributes": {"placementType": EVENTO[tipo], "placementGroup": "DEFAULT_PROFILE"},
        "relationships": {"image": {"data": {"type": "appAssetLibraryImages", "id": asset_id}},
                          "appEventLocalization": {"data": {"type": "appEventLocalizations", "id": localization_id}}}}})
