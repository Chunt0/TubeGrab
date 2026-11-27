"""Favorites: hearted artists, albums and songs."""

from flask import Blueprint, jsonify, request

from ..httputil import _cached_json, _json_bytes
from ..favorites import favorites, set_favorite


bp = Blueprint("favorites", __name__)


@bp.get("/api/favorites")
def api_favorites():
    return _cached_json(_json_bytes(favorites()))


@bp.post("/api/favorites")
def api_favorite():
    data = request.get_json(force=True, silent=True) or {}
    err = set_favorite(data.get("kind"), data.get("id"), bool(data.get("on", True)))
    if err:
        return jsonify(error=err), 400
    return jsonify(favorites())
