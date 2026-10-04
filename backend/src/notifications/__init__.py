"""Authenticated notification WebSocket interface.

Owns the versioned socket protocol, connection lifecycle, and user-targeted delivery seam so
callers do not depend on Channels routing or channel-layer event details.
"""
