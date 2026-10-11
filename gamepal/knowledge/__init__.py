"""Conocimiento del juego: fuentes por URL o fichero, índice local y búsqueda híbrida."""

from gamepal.knowledge.service import KnowledgeError, KnowledgeService
from gamepal.knowledge.store import Hit, KnowledgeStore

__all__ = ["Hit", "KnowledgeError", "KnowledgeService", "KnowledgeStore"]
