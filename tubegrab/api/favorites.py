"""Favorites and dislikes: hearted / disliked artists, albums and songs."""

from flask import Blueprint, jsonify, request

from ..httputil import _cached_json, _json_bytes
from ..favorites import marked, set_mark


bp = Blueprint("favorites", __name__)
ROUTES = {"favorites": "fav", "dislikes": "dislike"}


@bp.get("/api/<any(favorites, dislikes):which>")
def api_marked(which: str):
    return _cached_json(_json_bytes(marked(ROUTES[which])))


@bp.post("/api/<any(favorites, dislikes):which>")
def api_mark(which: str):
    """{kind: artist|album|song, id, on}. Marking one clears the other (a heart
    takes a dislike away); the answer is this list."""
    data = request.get_json(force=True, silent=True) or {}
    err = set_mark(ROUTES[which], data.get("kind"), data.get("id"), bool(data.get("on", True)))
    if err:
        return jsonify(error=err), 400
    return jsonify(marked(ROUTES[which]))
