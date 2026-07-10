"""APIRouter modules, one per concern, included by main.py.

Handlers read app state via request.app.state (the FastAPI-idiomatic way for a
router that does not own the app), so these modules never import main and stay
free of the composition-root import cycle. Cache/serving helpers come from the
leaf cache module.
"""
